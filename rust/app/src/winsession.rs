//! Windows: Libre Panel stops in order when the session ends (shut down, restart, sign out),
//! the panel sleeps with the computer, and it dims while the session is locked or the monitor
//! is off (winsession.py). A hidden window of its own hears it from Windows.

use std::sync::atomic::{AtomicBool, Ordering};

/// Set from the moment Windows asks whether the session may end, until it ends or another
/// program cancels it.
static ENDING: AtomicBool = AtomicBool::new(false);

/// ending: whether the Windows session is ending (shut down, restart, sign out): start no
/// program.
pub fn ending() -> bool {
    ENDING.load(Ordering::Relaxed)
}

#[cfg(windows)]
/// What the session's end, the computer's sleep and its wake do. They run on the window's
/// thread and may block: Windows waits for the answer to WM_ENDSESSION before it ends the
/// process (and gives a sleep about two seconds).
pub struct Handlers {
    pub on_end: Box<dyn Fn() + Send + Sync>,
    pub on_sleep: Box<dyn Fn() + Send + Sync>,
    pub on_wake: Box<dyn Fn() + Send + Sync>,
}

#[cfg(windows)]
pub use windows::{shut_down_early, Watcher};

#[cfg(windows)]
mod windows {
    use std::sync::atomic::{AtomicBool, AtomicIsize, Ordering};
    use std::sync::OnceLock;
    use std::time::Duration;

    use windows_sys::core::GUID;
    use windows_sys::Win32::Foundation::{HWND, LPARAM, LRESULT, WPARAM};
    use windows_sys::Win32::System::LibraryLoader::GetModuleHandleW;
    use windows_sys::Win32::System::Power::{
        RegisterPowerSettingNotification, RegisterSuspendResumeNotification, POWERBROADCAST_SETTING,
    };
    use windows_sys::Win32::System::RemoteDesktop::WTSRegisterSessionNotification;
    use windows_sys::Win32::System::Threading::SetProcessShutdownParameters;
    use windows_sys::Win32::UI::WindowsAndMessaging::{
        CreateWindowExW, DefWindowProcW, DispatchMessageW, GetMessageW, PostMessageW,
        PostQuitMessage, RegisterClassW, TranslateMessage, UnregisterClassW, MSG, WM_CLOSE,
        WM_DESTROY, WNDCLASSW,
    };

    use super::{Handlers, ENDING};

    const WM_QUERYENDSESSION: u32 = 0x0011;
    const WM_ENDSESSION: u32 = 0x0016;
    const WM_POWERBROADCAST: u32 = 0x0218;
    /// the computer goes to sleep
    const PBT_APMSUSPEND: usize = 0x0004;
    /// it woke, a person at it
    const PBT_APMRESUMESUSPEND: usize = 0x0007;
    /// it woke (always sent)
    const PBT_APMRESUMEAUTOMATIC: usize = 0x0012;
    /// a power setting asked for changed: the monitor's state
    const PBT_POWERSETTINGCHANGE: usize = 0x8013;
    const WM_WTSSESSION_CHANGE: u32 = 0x02B1;
    const WTS_SESSION_LOCK: usize = 0x7;
    const WTS_SESSION_UNLOCK: usize = 0x8;
    /// GUID_CONSOLE_DISPLAY_STATE 6fe69556-704a-47a0-8f24-c28d936fda47
    const CONSOLE_DISPLAY_STATE: GUID = GUID {
        data1: 0x6fe6_9556,
        data2: 0x704a,
        data3: 0x47a0,
        data4: [0x8f, 0x24, 0xc2, 0x8d, 0x93, 0x6f, 0xda, 0x47],
    };
    const EARLY_SHUTDOWN_LEVEL: u32 = 0x3FF;
    const DEVICE_NOTIFY_WINDOW_HANDLE: u32 = 0;

    static HANDLERS: OnceLock<Handlers> = OnceLock::new();
    static ENDED: AtomicBool = AtomicBool::new(false);

    fn debug(message: &str) {
        crate::logs::log(crate::logs::Level::Debug, "libre_panel.winsession", message);
    }

    /// shut_down_early: be among the first programs Windows tells about the end of the
    /// session.
    pub fn shut_down_early() {
        // SAFETY: a plain call with numbers
        if unsafe { SetProcessShutdownParameters(EARLY_SHUTDOWN_LEVEL, 0) } == 0 {
            debug(&format!(
                "SetProcessShutdownParameters failed ({})",
                std::io::Error::last_os_error()
            ));
        }
    }

    fn wide(text: &str) -> Vec<u16> {
        text.encode_utf16().chain(Some(0)).collect()
    }

    fn handlers() -> Option<&'static Handlers> {
        HANDLERS.get()
    }

    /// The session ends: the app stops (once), Windows waiting for the answer.
    fn end() {
        if ENDED.swap(true, Ordering::Relaxed) {
            return;
        }
        if let Some(handlers) = handlers() {
            (handlers.on_end)();
        }
    }

    /// The computer goes to sleep or wakes.
    fn power(event: usize) {
        let Some(handlers) = handlers() else { return };
        match event {
            PBT_APMSUSPEND => (handlers.on_sleep)(),
            PBT_APMRESUMESUSPEND | PBT_APMRESUMEAUTOMATIC => (handlers.on_wake)(),
            _ => {}
        }
    }

    /// A power setting changed: `lparam` points to a POWERBROADCAST_SETTING; the monitor's
    /// state is a DWORD: off (0), on (1) or dimmed (2: still on).
    fn setting(lparam: LPARAM) {
        if lparam == 0 {
            return;
        }
        // SAFETY: Windows hands the window a POWERBROADCAST_SETTING with this message
        let told = unsafe { &*(lparam as *const POWERBROADCAST_SETTING) };
        let same = |a: &GUID, b: &GUID| {
            a.data1 == b.data1 && a.data2 == b.data2 && a.data3 == b.data3 && a.data4 == b.data4
        };
        if !same(&told.PowerSetting, &CONSOLE_DISPLAY_STATE) || told.DataLength < 1 {
            return;
        }
        // SAFETY: DataLength bytes follow the header, at least one
        let data = unsafe {
            std::slice::from_raw_parts(told.Data.as_ptr(), (told.DataLength as usize).min(4))
        };
        let state = data
            .iter()
            .rev()
            .fold(0u32, |n, &b| (n << 8) | u32::from(b));
        crate::presence::set_away(crate::presence::DISPLAY_OFF, state == 0);
    }

    unsafe extern "system" fn procedure(
        hwnd: HWND,
        message: u32,
        wparam: WPARAM,
        lparam: LPARAM,
    ) -> LRESULT {
        match message {
            WM_QUERYENDSESSION => {
                ENDING.store(true, Ordering::Relaxed);
                1 // (Libre Panel never holds up the end of a session)
            }
            WM_ENDSESSION => {
                if wparam != 0 {
                    // (the session really ends: not cancelled)
                    ENDING.store(true, Ordering::Relaxed);
                    end();
                } else {
                    ENDING.store(false, Ordering::Relaxed);
                }
                0
            }
            WM_POWERBROADCAST => {
                match wparam {
                    PBT_POWERSETTINGCHANGE => setting(lparam),
                    event => power(event),
                }
                1 // (TRUE: Libre Panel never holds up a sleep)
            }
            WM_WTSSESSION_CHANGE => {
                if matches!(wparam, WTS_SESSION_LOCK | WTS_SESSION_UNLOCK) {
                    let locked = wparam == WTS_SESSION_LOCK;
                    crate::presence::set_away(crate::presence::LOCKED, locked);
                }
                0
            }
            WM_DESTROY => {
                // SAFETY: a plain call
                unsafe { PostQuitMessage(0) };
                0
            }
            // SAFETY: what the window does not answer itself, as Windows does it
            _ => unsafe { DefWindowProcW(hwnd, message, wparam, lparam) },
        }
    }

    /// SessionEndWatcher: the hidden window, on a thread of its own.
    pub struct Watcher {
        hwnd: std::sync::Arc<AtomicIsize>,
        thread: Option<std::thread::JoinHandle<()>>,
    }

    impl Watcher {
        /// start: the window made and heard (one in the process).
        pub fn start(handlers: Handlers) -> std::io::Result<Watcher> {
            if HANDLERS.set(handlers).is_err() {
                return Err(std::io::Error::other("the session window is there already"));
            }
            let hwnd = std::sync::Arc::new(AtomicIsize::new(0));
            let made = std::sync::Arc::clone(&hwnd);
            let (ready, wait) = std::sync::mpsc::channel();
            let thread = std::thread::Builder::new()
                .name("session-end".into())
                .spawn(move || run(&made, &ready))?;
            match wait.recv_timeout(Duration::from_secs(5)) {
                Ok(true) => Ok(Watcher {
                    hwnd,
                    thread: Some(thread),
                }),
                _ => Err(std::io::Error::other("could not create the session window")),
            }
        }

        pub fn stop(&mut self) {
            let hwnd = self.hwnd.load(Ordering::Relaxed);
            if hwnd != 0 {
                // SAFETY: the window this watcher made, closed on its own thread
                unsafe { PostMessageW(hwnd as HWND, WM_CLOSE, 0, 0) };
            }
            if let Some(thread) = self.thread.take() {
                let _ = thread.join();
            }
        }
    }

    fn run(made: &AtomicIsize, ready: &std::sync::mpsc::Sender<bool>) {
        let name = wide("LibrePanelSession");
        // SAFETY: the calls of a window and its message loop, on this thread, with the
        // window class's name kept alive as long as the window
        unsafe {
            let instance = GetModuleHandleW(std::ptr::null());
            let class = WNDCLASSW {
                style: 0,
                lpfnWndProc: Some(procedure),
                cbClsExtra: 0,
                cbWndExtra: 0,
                hInstance: instance,
                hIcon: std::ptr::null_mut(),
                hCursor: std::ptr::null_mut(),
                hbrBackground: std::ptr::null_mut(),
                lpszMenuName: std::ptr::null(),
                lpszClassName: name.as_ptr(),
            };
            if RegisterClassW(&class) == 0 {
                crate::logs::say(
                    "libre_panel.winsession",
                    &format!(
                        "session window: RegisterClass failed ({})",
                        std::io::Error::last_os_error()
                    ),
                );
                let _ = ready.send(false);
                return;
            }
            // (a hidden top-level window: message-only windows get no session messages)
            let title = wide("Libre Panel");
            let hwnd = CreateWindowExW(
                0,
                name.as_ptr(),
                title.as_ptr(),
                0,
                0,
                0,
                0,
                0,
                std::ptr::null_mut(),
                std::ptr::null_mut(),
                instance,
                std::ptr::null(),
            );
            if hwnd.is_null() {
                crate::logs::say(
                    "libre_panel.winsession",
                    &format!(
                        "session window: CreateWindow failed ({})",
                        std::io::Error::last_os_error()
                    ),
                );
                let _ = ready.send(false);
                return;
            }
            made.store(hwnd as isize, Ordering::Relaxed);
            // (modern standby tells only who asks)
            RegisterSuspendResumeNotification(hwnd, DEVICE_NOTIFY_WINDOW_HANDLE);
            if WTSRegisterSessionNotification(hwnd, 0) == 0 {
                debug("no word of locking");
            }
            if RegisterPowerSettingNotification(
                hwnd,
                &CONSOLE_DISPLAY_STATE,
                DEVICE_NOTIFY_WINDOW_HANDLE,
            ) == 0
            {
                debug("no word of the monitor");
            }
            let _ = ready.send(true);
            let mut message: MSG = std::mem::zeroed();
            while GetMessageW(&mut message, std::ptr::null_mut(), 0, 0) > 0 {
                TranslateMessage(&message);
                DispatchMessageW(&message);
            }
            UnregisterClassW(name.as_ptr(), instance);
        }
    }
}
