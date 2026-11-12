//! A program started with its input, output and error piped, as Python's subprocess.Popen
//! starts one with close_fds (its default): on Windows the new process inherits those three
//! pipes and nothing else, named to CreateProcessW in a list of the handles it may take
//! (PROC_THREAD_ATTRIBUTE_HANDLE_LIST). Rust's Command lets the new process take every handle
//! of the app's that may be inherited; a connection of the app's server open at that moment,
//! held by a plugin host that runs for as long as the app, would never end for the browser
//! that waits for its end. Elsewhere Rust's Command, whose handles are all closed on exec.

use std::io::{Read, Write};
use std::path::Path;
use std::process::{Child, Command, Stdio};

/// A started program's pipes and process.
pub struct Piped {
    pub stdin: Box<dyn Write + Send>,
    pub stdout: Box<dyn Read + Send>,
    pub stderr: Box<dyn Read + Send>,
    pub process: Process,
}

/// A started program, to be stopped and waited for.
pub enum Process {
    Std(Child),
    #[cfg(windows)]
    Own(windows::Own),
}

impl Process {
    pub fn id(&self) -> u32 {
        match self {
            Process::Std(child) => child.id(),
            #[cfg(windows)]
            Process::Own(own) => own.id,
        }
    }

    pub fn kill(&mut self) {
        match self {
            Process::Std(child) => {
                let _ = child.kill();
            }
            #[cfg(windows)]
            Process::Own(own) => own.kill(),
        }
    }

    pub fn wait(&mut self) {
        match self {
            Process::Std(child) => {
                let _ = child.wait();
            }
            #[cfg(windows)]
            Process::Own(own) => own.wait(),
        }
    }
}

/// `program` with `args`, its three streams piped; `flags` the process creation flags of
/// Windows (CREATE_NO_WINDOW and the like; nothing elsewhere).
pub fn piped(program: &Path, args: &[String], flags: u32) -> std::io::Result<Piped> {
    #[cfg(windows)]
    {
        if program.is_absolute() {
            return windows::piped(program, args, flags);
        }
    }
    let mut command = Command::new(program);
    command
        .args(args)
        .stdin(Stdio::piped())
        .stdout(Stdio::piped())
        .stderr(Stdio::piped());
    #[cfg(windows)]
    {
        use std::os::windows::process::CommandExt;
        command.creation_flags(flags);
    }
    #[cfg(not(windows))]
    let _ = flags;
    let mut child = command.spawn()?;
    let missing = || std::io::Error::other("a pipe of the started program is missing");
    let stdin = child.stdin.take().ok_or_else(missing)?;
    let stdout = child.stdout.take().ok_or_else(missing)?;
    let stderr = child.stderr.take().ok_or_else(missing)?;
    Ok(Piped {
        stdin: Box::new(stdin),
        stdout: Box::new(stdout),
        stderr: Box::new(stderr),
        process: Process::Std(child),
    })
}

/// list2cmdline's way (and Rust's): an argument as the C runtime of the started program reads
/// it back, quoted where it is empty or has a space or tab in it, the backslashes before a
/// quote and before the closing quote doubled.
#[cfg_attr(not(any(windows, test)), allow(dead_code))]
fn quoted(arg: &str, line: &mut String) {
    let quote = arg.is_empty() || arg.contains([' ', '\t']);
    if quote {
        line.push('"');
    }
    let mut backslashes = 0usize;
    for c in arg.chars() {
        if c == '\\' {
            backslashes += 1;
        } else {
            if c == '"' {
                line.extend(std::iter::repeat_n('\\', backslashes + 1));
            }
            backslashes = 0;
        }
        line.push(c);
    }
    if quote {
        line.extend(std::iter::repeat_n('\\', backslashes));
        line.push('"');
    }
}

/// The command line of `program` and `args`: the program's name in quotes (as Rust and
/// Python's subprocess give it), each argument quoted where it needs it, a space between.
#[cfg_attr(not(any(windows, test)), allow(dead_code))]
fn command_line(program: &str, args: &[String]) -> std::io::Result<String> {
    if program.contains('"') {
        return Err(std::io::Error::other("a program's name with a quote in it"));
    }
    let mut line = format!("\"{program}\"");
    for arg in args {
        line.push(' ');
        quoted(arg, &mut line);
    }
    Ok(line)
}

#[cfg(windows)]
mod windows {
    use super::{command_line, Piped, Process};
    use std::ffi::c_void;
    use std::fs::File;
    use std::os::windows::ffi::OsStrExt;
    use std::os::windows::io::FromRawHandle;
    use std::path::Path;
    use windows_sys::Win32::Foundation::{
        CloseHandle, SetHandleInformation, HANDLE, HANDLE_FLAG_INHERIT,
    };
    use windows_sys::Win32::Security::SECURITY_ATTRIBUTES;
    use windows_sys::Win32::System::Pipes::CreatePipe;
    use windows_sys::Win32::System::Threading::{
        CreateProcessW, DeleteProcThreadAttributeList, InitializeProcThreadAttributeList,
        TerminateProcess, UpdateProcThreadAttribute, WaitForSingleObject,
        EXTENDED_STARTUPINFO_PRESENT, INFINITE, PROCESS_INFORMATION,
        PROC_THREAD_ATTRIBUTE_HANDLE_LIST, STARTF_USESTDHANDLES, STARTUPINFOEXW,
    };

    /// A process started here: its handle (closed with it) and its number.
    pub struct Own {
        handle: HANDLE,
        pub id: u32,
    }

    // SAFETY: a process handle may be used from any thread.
    unsafe impl Send for Own {}

    impl Own {
        pub fn kill(&mut self) {
            // SAFETY: the process's own handle, open while self lives.
            unsafe { TerminateProcess(self.handle, 1) };
        }

        pub fn wait(&mut self) {
            // SAFETY: as in kill.
            unsafe { WaitForSingleObject(self.handle, INFINITE) };
        }
    }

    impl Drop for Own {
        fn drop(&mut self) {
            // SAFETY: as in kill; not used after.
            unsafe { CloseHandle(self.handle) };
        }
    }

    /// A handle closed when it goes, where it is not handed on.
    struct Held(HANDLE);

    impl Held {
        /// The handle as a File of its own (no longer closed here).
        fn into_file(self) -> File {
            let handle = self.0;
            std::mem::forget(self);
            // SAFETY: a pipe's end made by CreatePipe, owned here alone.
            unsafe { File::from_raw_handle(handle) }
        }
    }

    impl Drop for Held {
        fn drop(&mut self) {
            // SAFETY: a handle owned here alone.
            unsafe { CloseHandle(self.0) };
        }
    }

    /// A pipe: its end for the started program, which it may inherit, and the app's, which
    /// no program inherits.
    fn pipe(child_reads: bool) -> std::io::Result<(Held, Held)> {
        let attributes = SECURITY_ATTRIBUTES {
            nLength: std::mem::size_of::<SECURITY_ATTRIBUTES>() as u32,
            lpSecurityDescriptor: std::ptr::null_mut(),
            bInheritHandle: 1,
        };
        let (mut read, mut write): (HANDLE, HANDLE) = (std::ptr::null_mut(), std::ptr::null_mut());
        // SAFETY: the out pointers are to locals; the attributes live through the call.
        if unsafe { CreatePipe(&mut read, &mut write, &attributes, 0) } == 0 {
            return Err(std::io::Error::last_os_error());
        }
        let (read, write) = (Held(read), Held(write));
        let (theirs, ours) = if child_reads {
            (read, write)
        } else {
            (write, read)
        };
        // SAFETY: a handle CreatePipe just made.
        if unsafe { SetHandleInformation(ours.0, HANDLE_FLAG_INHERIT, 0) } == 0 {
            return Err(std::io::Error::last_os_error());
        }
        Ok((theirs, ours))
    }

    fn wide(text: &std::ffi::OsStr) -> Vec<u16> {
        text.encode_wide().chain(std::iter::once(0)).collect()
    }

    pub fn piped(program: &Path, args: &[String], flags: u32) -> std::io::Result<Piped> {
        let name = program
            .to_str()
            .ok_or_else(|| std::io::Error::other("a program's name that is no Unicode"))?;
        let mut line = wide(command_line(name, args)?.as_ref());
        let application = wide(program.as_os_str());
        let (in_theirs, in_ours) = pipe(true)?;
        let (out_theirs, out_ours) = pipe(false)?;
        let (err_theirs, err_ours) = pipe(false)?;
        let handles = [in_theirs.0, out_theirs.0, err_theirs.0];
        // the list of the handles it may inherit: these three alone
        let mut size = 0usize;
        // SAFETY: asks the size alone (no list given).
        unsafe { InitializeProcThreadAttributeList(std::ptr::null_mut(), 1, 0, &mut size) };
        let mut room = vec![0u64; size.div_ceil(8).max(1)];
        let list = room.as_mut_ptr().cast::<c_void>();
        // SAFETY: `room` is `size` bytes at least, aligned for the list.
        if unsafe { InitializeProcThreadAttributeList(list, 1, 0, &mut size) } == 0 {
            return Err(std::io::Error::last_os_error());
        }
        struct List(*mut c_void);
        impl Drop for List {
            fn drop(&mut self) {
                // SAFETY: a list initialized above, its room still held.
                unsafe { DeleteProcThreadAttributeList(self.0) };
            }
        }
        let list = List(list);
        // SAFETY: the list was initialized for one attribute; `handles` outlives the
        // CreateProcessW below.
        let updated = unsafe {
            UpdateProcThreadAttribute(
                list.0,
                0,
                PROC_THREAD_ATTRIBUTE_HANDLE_LIST as usize,
                handles.as_ptr().cast(),
                std::mem::size_of_val(&handles),
                std::ptr::null_mut(),
                std::ptr::null(),
            )
        };
        if updated == 0 {
            return Err(std::io::Error::last_os_error());
        }
        // SAFETY: STARTUPINFOEXW is plain data, zero a valid start.
        let mut start: STARTUPINFOEXW = unsafe { std::mem::zeroed() };
        start.StartupInfo.cb = std::mem::size_of::<STARTUPINFOEXW>() as u32;
        start.StartupInfo.dwFlags = STARTF_USESTDHANDLES;
        start.StartupInfo.hStdInput = in_theirs.0;
        start.StartupInfo.hStdOutput = out_theirs.0;
        start.StartupInfo.hStdError = err_theirs.0;
        start.lpAttributeList = list.0;
        // SAFETY: as STARTUPINFOEXW.
        let mut info: PROCESS_INFORMATION = unsafe { std::mem::zeroed() };
        // SAFETY: the names are NUL-ended and live through the call, the command line
        // writable as CreateProcessW wants it; the start's handles and list are alive.
        let made = unsafe {
            CreateProcessW(
                application.as_ptr(),
                line.as_mut_ptr(),
                std::ptr::null(),
                std::ptr::null(),
                1,
                EXTENDED_STARTUPINFO_PRESENT | flags,
                std::ptr::null(),
                std::ptr::null(),
                &start.StartupInfo,
                &mut info,
            )
        };
        if made == 0 {
            return Err(std::io::Error::last_os_error());
        }
        drop(list);
        // (the started program's ends are its own now: closed here)
        drop((in_theirs, out_theirs, err_theirs));
        // SAFETY: the thread's handle CreateProcessW gave, not used.
        unsafe { CloseHandle(info.hThread) };
        Ok(Piped {
            stdin: Box::new(in_ours.into_file()),
            stdout: Box::new(out_ours.into_file()),
            stderr: Box::new(err_ours.into_file()),
            process: Process::Own(Own {
                handle: info.hProcess,
                id: info.dwProcessId,
            }),
        })
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn arguments_are_quoted_as_list2cmdline_quotes_them() {
        let line = |args: &[&str]| {
            let args: Vec<String> = args.iter().map(|a| a.to_string()).collect();
            command_line(r"C:\Program Files\Python\python.exe", &args).unwrap()
        };
        assert_eq!(
            line(&[
                "-m",
                "libre_panel.plugin_host",
                "--folder",
                r"C:\Program Files\x\plugins"
            ]),
            r#""C:\Program Files\Python\python.exe" -m libre_panel.plugin_host --folder "C:\Program Files\x\plugins""#
        );
        assert_eq!(line(&[""]), r#""C:\Program Files\Python\python.exe" """#);
        assert_eq!(
            line(&[r#"a"b"#]),
            r#""C:\Program Files\Python\python.exe" a\"b"#
        );
        assert_eq!(
            line(&[r"a\\b"]),
            r#""C:\Program Files\Python\python.exe" a\\b"#
        );
        assert_eq!(
            line(&[r"a b\"]),
            r#""C:\Program Files\Python\python.exe" "a b\\""#
        );
        assert_eq!(
            line(&[r#"a\"b"#]),
            r#""C:\Program Files\Python\python.exe" a\\\"b"#
        );
        assert!(command_line("a\"b", &[]).is_err());
    }

    #[test]
    fn a_program_is_started_with_its_streams_piped() {
        // (what it reads written back: sh's cat, cmd's more)
        let (program, args): (std::path::PathBuf, Vec<String>) = if cfg!(windows) {
            let root = std::env::var("SystemRoot").unwrap_or_else(|_| r"C:\Windows".into());
            (
                Path::new(&root).join(r"System32\cmd.exe"),
                vec!["/d".into(), "/c".into(), "more".into()],
            )
        } else {
            (
                Path::new("/bin/sh").into(),
                vec!["-c".into(), "cat; echo err >&2".into()],
            )
        };
        let mut piped = piped(&program, &args, 0).unwrap();
        assert!(piped.process.id() > 0);
        piped.stdin.write_all(b"one\n").unwrap();
        drop(piped.stdin);
        let mut out = String::new();
        piped.stdout.read_to_string(&mut out).unwrap();
        let mut err = String::new();
        piped.stderr.read_to_string(&mut err).unwrap();
        piped.process.wait();
        assert_eq!(out.trim_end(), "one");
        assert_eq!(err.trim_end(), if cfg!(windows) { "" } else { "err" });
    }
}
