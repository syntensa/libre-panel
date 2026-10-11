"""The Windows calls of the sensor service and of what talks to it, through ctypes.

Imported only on Windows, and only when one of them is needed: the pipe (``winpipe``), the
service host (``winservice``), the Service Control Manager, the accounts and groups that
``libre-panel system-setup`` changes, and starting a program with a UAC prompt. Everything
here is a thin wrapper that raises ``OSError`` with Windows' own error code; the logic that
uses it is elsewhere and tested with fakes.
"""

from __future__ import annotations

import contextlib
import ctypes
import functools
from collections.abc import Iterator
from ctypes import wintypes as w
from typing import Any

ULONG_PTR = ctypes.c_size_t
INVALID_HANDLE = ctypes.c_void_p(-1).value
INFINITE = 0xFFFFFFFF
WAIT_OBJECT_0, WAIT_TIMEOUT = 0, 0x102

# Win32 error codes
ERROR_FILE_NOT_FOUND = 2
ERROR_ACCESS_DENIED = 5
ERROR_INSUFFICIENT_BUFFER = 122
ERROR_BROKEN_PIPE = 109
ERROR_PIPE_BUSY = 231
ERROR_NO_DATA = 232
ERROR_PIPE_NOT_CONNECTED = 233
ERROR_MORE_DATA = 234
ERROR_PIPE_CONNECTED = 535
ERROR_OPERATION_ABORTED = 995
ERROR_IO_PENDING = 997
ERROR_SERVICE_ALREADY_RUNNING = 1056
ERROR_SERVICE_DOES_NOT_EXIST = 1060
ERROR_FAILED_SERVICE_CONTROLLER_CONNECT = 1063
ERROR_CANCELLED = 1223
ERROR_MEMBER_NOT_IN_ALIAS = 1377
ERROR_MEMBER_IN_ALIAS = 1378

# Named pipes
PIPE_ACCESS_DUPLEX = 0x3
FILE_FLAG_FIRST_PIPE_INSTANCE = 0x00080000
FILE_FLAG_OVERLAPPED = 0x40000000
PIPE_TYPE_BYTE = PIPE_READMODE_BYTE = PIPE_WAIT = 0
PIPE_REJECT_REMOTE_CLIENTS = 0x8
OPEN_EXISTING = 3
SECURITY_SQOS_PRESENT = 0x00100000
SECURITY_IDENTIFICATION = 0x00010000  # the server may tell who the client is, not act as it
# What a client asks for: read and write the pipe's data and read its attributes. Not
# GENERIC_WRITE, whose FILE_APPEND_DATA is, on a pipe, the right to make another instance.
FILE_READ_DATA, FILE_WRITE_DATA, FILE_READ_ATTRIBUTES = 0x1, 0x2, 0x80
READ_CONTROL, SYNCHRONIZE = 0x20000, 0x100000
CLIENT_ACCESS = FILE_READ_DATA | FILE_WRITE_DATA | FILE_READ_ATTRIBUTES | SYNCHRONIZE

# Services
SC_MANAGER_CONNECT = 0x1
SERVICE_QUERY_STATUS, SERVICE_START = 0x4, 0x10
SC_STATUS_PROCESS_INFO = 0
SERVICE_WIN32_OWN_PROCESS = 0x10
SERVICE_STOPPED, SERVICE_START_PENDING, SERVICE_STOP_PENDING, SERVICE_RUNNING = 1, 2, 3, 4
SERVICE_ACCEPT_STOP, SERVICE_ACCEPT_SHUTDOWN = 0x1, 0x4
SERVICE_CONTROL_STOP, SERVICE_CONTROL_INTERROGATE, SERVICE_CONTROL_SHUTDOWN = 0x1, 0x4, 0x5
NO_ERROR, ERROR_CALL_NOT_IMPLEMENTED = 0, 120
STATE_NAMES = {
    1: "stopped",
    2: "starting",
    3: "stopping",
    4: "running",
    5: "continuing",
    6: "pausing",
    7: "paused",
}

# Tokens, sessions, starting a program as administrator
TOKEN_QUERY = 0x8
TOKEN_USER_CLASS = 1
WTS_USER_NAME, WTS_DOMAIN_NAME = 5, 7
SEE_MASK_NOCLOSEPROCESS, SEE_MASK_NOASYNC, SEE_MASK_FLAG_NO_UI = 0x40, 0x100, 0x400
SW_HIDE = 0
MAX_PREFERRED_LENGTH = 0xFFFFFFFF


class OVERLAPPED(ctypes.Structure):
    _fields_ = [
        ("Internal", ULONG_PTR),
        ("InternalHigh", ULONG_PTR),
        ("Offset", w.DWORD),
        ("OffsetHigh", w.DWORD),
        ("hEvent", w.HANDLE),
    ]


class SECURITY_ATTRIBUTES(ctypes.Structure):  # noqa: N801 - Windows' name
    _fields_ = [
        ("nLength", w.DWORD),
        ("lpSecurityDescriptor", w.LPVOID),
        ("bInheritHandle", w.BOOL),
    ]


_STATUS_FIELDS = [
    ("dwServiceType", w.DWORD),
    ("dwCurrentState", w.DWORD),
    ("dwControlsAccepted", w.DWORD),
    ("dwWin32ExitCode", w.DWORD),
    ("dwServiceSpecificExitCode", w.DWORD),
    ("dwCheckPoint", w.DWORD),
    ("dwWaitHint", w.DWORD),
]


class SERVICE_STATUS(ctypes.Structure):  # noqa: N801
    _fields_ = _STATUS_FIELDS


class SERVICE_STATUS_PROCESS(ctypes.Structure):  # noqa: N801
    _fields_ = [*_STATUS_FIELDS, ("dwProcessId", w.DWORD), ("dwServiceFlags", w.DWORD)]


# (CFUNCTYPE where there is no WINFUNCTYPE: this module is for Windows, but importing it
# elsewhere does no harm)
_WINFUNCTYPE = getattr(ctypes, "WINFUNCTYPE", ctypes.CFUNCTYPE)
SERVICE_MAIN_FUNCTION = _WINFUNCTYPE(None, w.DWORD, ctypes.POINTER(w.LPWSTR))
HANDLER_FUNCTION_EX = _WINFUNCTYPE(w.DWORD, w.DWORD, w.DWORD, w.LPVOID, w.LPVOID)


class SERVICE_TABLE_ENTRYW(ctypes.Structure):  # noqa: N801
    _fields_ = [("lpServiceName", w.LPWSTR), ("lpServiceProc", SERVICE_MAIN_FUNCTION)]


class SHELLEXECUTEINFOW(ctypes.Structure):  # noqa: N801
    _fields_ = [
        ("cbSize", w.DWORD),
        ("fMask", w.ULONG),
        ("hwnd", w.HWND),
        ("lpVerb", w.LPCWSTR),
        ("lpFile", w.LPCWSTR),
        ("lpParameters", w.LPCWSTR),
        ("lpDirectory", w.LPCWSTR),
        ("nShow", ctypes.c_int),
        ("hInstApp", w.HINSTANCE),
        ("lpIDList", w.LPVOID),
        ("lpClass", w.LPCWSTR),
        ("hkeyClass", w.HKEY),
        ("dwHotKey", w.DWORD),
        ("hIconOrMonitor", w.HANDLE),
        ("hProcess", w.HANDLE),
    ]


class LOCALGROUP_MEMBERS_INFO_0(ctypes.Structure):  # noqa: N801
    _fields_ = [("lgrmi0_sid", w.LPVOID)]


class SID_AND_ATTRIBUTES(ctypes.Structure):  # noqa: N801
    _fields_ = [("Sid", w.LPVOID), ("Attributes", w.DWORD)]


class TOKEN_USER(ctypes.Structure):  # noqa: N801
    _fields_ = [("User", SID_AND_ATTRIBUTES)]


def _declare(dll: Any, name: str, restype: Any, *argtypes: Any) -> None:
    function = getattr(dll, name)
    function.argtypes = list(argtypes)
    function.restype = restype


@functools.cache
def kernel32() -> Any:
    k = ctypes.WinDLL("kernel32", use_last_error=True)  # type: ignore[attr-defined]
    lp_overlapped = ctypes.POINTER(OVERLAPPED)
    _declare(
        k, "CreateNamedPipeW", w.HANDLE, w.LPCWSTR, w.DWORD, w.DWORD, w.DWORD, w.DWORD,
        w.DWORD, w.DWORD, ctypes.POINTER(SECURITY_ATTRIBUTES),
    )  # fmt: skip
    _declare(k, "ConnectNamedPipe", w.BOOL, w.HANDLE, lp_overlapped)
    _declare(k, "DisconnectNamedPipe", w.BOOL, w.HANDLE)
    _declare(
        k, "CreateFileW", w.HANDLE, w.LPCWSTR, w.DWORD, w.DWORD, w.LPVOID, w.DWORD, w.DWORD,
        w.HANDLE,
    )  # fmt: skip
    _declare(k, "WaitNamedPipeW", w.BOOL, w.LPCWSTR, w.DWORD)
    _declare(k, "ReadFile", w.BOOL, w.HANDLE, w.LPVOID, w.DWORD, w.LPDWORD, lp_overlapped)
    _declare(k, "WriteFile", w.BOOL, w.HANDLE, w.LPCVOID, w.DWORD, w.LPDWORD, lp_overlapped)
    _declare(k, "GetOverlappedResult", w.BOOL, w.HANDLE, lp_overlapped, w.LPDWORD, w.BOOL)
    _declare(k, "CancelIoEx", w.BOOL, w.HANDLE, lp_overlapped)
    _declare(k, "CreateEventW", w.HANDLE, w.LPVOID, w.BOOL, w.BOOL, w.LPCWSTR)
    _declare(k, "SetEvent", w.BOOL, w.HANDLE)
    _declare(k, "ResetEvent", w.BOOL, w.HANDLE)
    _declare(
        k, "WaitForMultipleObjects", w.DWORD, w.DWORD, ctypes.POINTER(w.HANDLE), w.BOOL, w.DWORD
    )
    _declare(k, "WaitForSingleObject", w.DWORD, w.HANDLE, w.DWORD)
    _declare(k, "CloseHandle", w.BOOL, w.HANDLE)
    _declare(k, "GetNamedPipeServerProcessId", w.BOOL, w.HANDLE, ctypes.POINTER(w.ULONG))
    _declare(k, "LocalFree", w.HLOCAL, w.HLOCAL)
    _declare(k, "GetCurrentProcess", w.HANDLE)
    _declare(k, "GetCurrentProcessId", w.DWORD)
    _declare(k, "ProcessIdToSessionId", w.BOOL, w.DWORD, w.LPDWORD)
    _declare(k, "GetExitCodeProcess", w.BOOL, w.HANDLE, w.LPDWORD)
    _declare(k, "GetSystemDirectoryW", w.UINT, w.LPWSTR, w.UINT)
    return k


@functools.cache
def advapi32() -> Any:
    a = ctypes.WinDLL("advapi32", use_last_error=True)  # type: ignore[attr-defined]
    _declare(
        a, "ConvertStringSecurityDescriptorToSecurityDescriptorW", w.BOOL, w.LPCWSTR, w.DWORD,
        ctypes.POINTER(w.LPVOID), ctypes.POINTER(w.ULONG),
    )  # fmt: skip
    _declare(a, "ConvertSidToStringSidW", w.BOOL, w.LPVOID, ctypes.POINTER(w.LPWSTR))
    _declare(a, "ConvertStringSidToSidW", w.BOOL, w.LPCWSTR, ctypes.POINTER(w.LPVOID))
    lookup = (w.LPWSTR, w.LPDWORD, w.LPWSTR, w.LPDWORD, w.LPDWORD)
    _declare(a, "LookupAccountSidW", w.BOOL, w.LPCWSTR, w.LPVOID, *lookup)
    _declare(
        a, "LookupAccountNameW", w.BOOL, w.LPCWSTR, w.LPCWSTR, w.LPVOID, w.LPDWORD, w.LPWSTR,
        w.LPDWORD, w.LPDWORD,
    )  # fmt: skip
    _declare(a, "OpenProcessToken", w.BOOL, w.HANDLE, w.DWORD, ctypes.POINTER(w.HANDLE))
    _declare(a, "GetTokenInformation", w.BOOL, w.HANDLE, ctypes.c_int, w.LPVOID, w.DWORD, w.LPDWORD)
    _declare(a, "OpenSCManagerW", w.HANDLE, w.LPCWSTR, w.LPCWSTR, w.DWORD)
    _declare(a, "OpenServiceW", w.HANDLE, w.HANDLE, w.LPCWSTR, w.DWORD)
    _declare(
        a, "QueryServiceStatusEx", w.BOOL, w.HANDLE, ctypes.c_int, w.LPVOID, w.DWORD, w.LPDWORD
    )
    _declare(a, "StartServiceW", w.BOOL, w.HANDLE, w.DWORD, w.LPVOID)
    _declare(a, "CloseServiceHandle", w.BOOL, w.HANDLE)
    _declare(a, "StartServiceCtrlDispatcherW", w.BOOL, ctypes.POINTER(SERVICE_TABLE_ENTRYW))
    _declare(a, "RegisterServiceCtrlHandlerExW", w.HANDLE, w.LPCWSTR, HANDLER_FUNCTION_EX, w.LPVOID)
    _declare(a, "SetServiceStatus", w.BOOL, w.HANDLE, ctypes.POINTER(SERVICE_STATUS))
    _declare(a, "CheckTokenMembership", w.BOOL, w.HANDLE, w.LPVOID, ctypes.POINTER(w.BOOL))
    return a


@functools.cache
def netapi32() -> Any:
    n = ctypes.WinDLL("netapi32", use_last_error=True)  # type: ignore[attr-defined]
    _declare(
        n, "NetLocalGroupAddMembers", w.DWORD, w.LPCWSTR, w.LPCWSTR, w.DWORD, w.LPVOID, w.DWORD
    )
    _declare(
        n, "NetLocalGroupDelMembers", w.DWORD, w.LPCWSTR, w.LPCWSTR, w.DWORD, w.LPVOID, w.DWORD
    )
    _declare(
        n, "NetLocalGroupGetMembers", w.DWORD, w.LPCWSTR, w.LPCWSTR, w.DWORD,
        ctypes.POINTER(w.LPVOID), w.DWORD, w.LPDWORD, w.LPDWORD, ctypes.POINTER(ULONG_PTR),
    )  # fmt: skip
    _declare(n, "NetApiBufferFree", w.DWORD, w.LPVOID)
    return n


@functools.cache
def wtsapi32() -> Any:
    t = ctypes.WinDLL("wtsapi32", use_last_error=True)  # type: ignore[attr-defined]
    _declare(
        t, "WTSQuerySessionInformationW", w.BOOL, w.HANDLE, w.DWORD, ctypes.c_int,
        ctypes.POINTER(w.LPWSTR), w.LPDWORD,
    )  # fmt: skip
    _declare(t, "WTSFreeMemory", None, w.LPVOID)
    return t


@functools.cache
def shell32() -> Any:
    s = ctypes.WinDLL("shell32", use_last_error=True)  # type: ignore[attr-defined]
    _declare(s, "ShellExecuteExW", w.BOOL, ctypes.POINTER(SHELLEXECUTEINFOW))
    return s


def last_error(what: str, code: int | None = None) -> OSError:
    """``OSError`` for the error the last call of this thread left (or ``code``)."""
    code = ctypes.get_last_error() if code is None else code
    return ctypes.WinError(code, f"{what}: {ctypes.FormatError(code).strip()}")  # type: ignore[attr-defined]


def close(handle: Any) -> None:
    if handle and handle != INVALID_HANDLE:
        kernel32().CloseHandle(handle)


def system_folder() -> str:
    """Windows' own folder of programs (System32), from Windows, not from the environment,
    which the person who starts a program may set as they like."""
    buffer = ctypes.create_unicode_buffer(300)
    if not kernel32().GetSystemDirectoryW(buffer, len(buffer)):
        raise last_error("GetSystemDirectory")
    return buffer.value


def new_event(manual: bool = True) -> Any:
    event = kernel32().CreateEventW(None, manual, False, None)
    if not event:
        raise last_error("CreateEvent")
    return event


# -- security descriptors and SIDs -----------------------------------------------------------------


@contextlib.contextmanager
def security_attributes(sddl: str) -> Iterator[SECURITY_ATTRIBUTES]:
    """SECURITY_ATTRIBUTES with the security descriptor that ``sddl`` describes."""
    descriptor = w.LPVOID()
    if not advapi32().ConvertStringSecurityDescriptorToSecurityDescriptorW(
        sddl, 1, ctypes.byref(descriptor), None
    ):
        raise last_error("ConvertStringSecurityDescriptorToSecurityDescriptor")
    try:
        yield SECURITY_ATTRIBUTES(ctypes.sizeof(SECURITY_ATTRIBUTES), descriptor, False)
    finally:
        kernel32().LocalFree(descriptor)


def sid_text(sid: Any) -> str:
    """A SID as text, "S-1-5-21-…"."""
    out = w.LPWSTR()
    if not advapi32().ConvertSidToStringSidW(sid, ctypes.byref(out)):
        raise last_error("ConvertSidToStringSid")
    try:
        return str(out.value)
    finally:
        kernel32().LocalFree(ctypes.cast(out, w.HLOCAL))


@contextlib.contextmanager
def sid_of(text: str) -> Iterator[Any]:
    """The binary SID of "S-1-…", for as long as the block runs."""
    sid = w.LPVOID()
    if not advapi32().ConvertStringSidToSidW(text, ctypes.byref(sid)):
        raise last_error(f"ConvertStringSidToSid {text}")
    try:
        yield sid
    finally:
        kernel32().LocalFree(sid)


def token_user_sid() -> str:
    """The SID of the account this process runs as."""
    token = w.HANDLE()
    if not advapi32().OpenProcessToken(
        kernel32().GetCurrentProcess(), TOKEN_QUERY, ctypes.byref(token)
    ):
        raise last_error("OpenProcessToken")
    try:
        size = w.DWORD(0)
        advapi32().GetTokenInformation(token, TOKEN_USER_CLASS, None, 0, ctypes.byref(size))
        buffer = ctypes.create_string_buffer(max(size.value, ctypes.sizeof(TOKEN_USER)))
        if not advapi32().GetTokenInformation(
            token, TOKEN_USER_CLASS, buffer, len(buffer), ctypes.byref(size)
        ):
            raise last_error("GetTokenInformation")
        user = ctypes.cast(buffer, ctypes.POINTER(TOKEN_USER)).contents
        return sid_text(user.User.Sid)
    finally:
        close(token)


def token_has(sid: str) -> bool:
    """Whether this process's token has the group: a group joined after signing in is not
    in it until the next sign-in."""
    member = w.BOOL(False)
    with sid_of(sid) as binary:
        if not advapi32().CheckTokenMembership(None, binary, ctypes.byref(member)):
            raise last_error("CheckTokenMembership")
    return bool(member.value)


def account_sid(account: str) -> str | None:
    """The SID of an account named "DOMAIN\\user" (or "user"); None if there is no such."""
    sid_size, domain_size, use = w.DWORD(0), w.DWORD(0), w.DWORD(0)
    advapi32().LookupAccountNameW(
        None, account, None, ctypes.byref(sid_size), None, ctypes.byref(domain_size),
        ctypes.byref(use),
    )  # fmt: skip
    if not sid_size.value:
        return None
    sid = ctypes.create_string_buffer(sid_size.value)
    domain = ctypes.create_unicode_buffer(max(1, domain_size.value))
    if not advapi32().LookupAccountNameW(
        None, account, sid, ctypes.byref(sid_size), domain, ctypes.byref(domain_size),
        ctypes.byref(use),
    ):  # fmt: skip
        return None
    return sid_text(sid)


def account_name(sid: str) -> str:
    """The name of the account or group with this SID, in the language of this Windows
    (the group S-1-5-32-559 is "Performance Log Users", or "Leistungsprotokollbenutzer")."""
    with sid_of(sid) as binary:
        name_size, domain_size, use = w.DWORD(0), w.DWORD(0), w.DWORD(0)
        advapi32().LookupAccountSidW(
            None, binary, None, ctypes.byref(name_size), None, ctypes.byref(domain_size),
            ctypes.byref(use),
        )  # fmt: skip
        name = ctypes.create_unicode_buffer(max(1, name_size.value))
        domain = ctypes.create_unicode_buffer(max(1, domain_size.value))
        if not advapi32().LookupAccountSidW(
            None, binary, name, ctypes.byref(name_size), domain, ctypes.byref(domain_size),
            ctypes.byref(use),
        ):  # fmt: skip
            raise last_error(f"LookupAccountSid {sid}")
        return name.value


def _session_text(session: int, kind: int) -> str:
    buffer, size = w.LPWSTR(), w.DWORD(0)
    if not wtsapi32().WTSQuerySessionInformationW(
        None, session, kind, ctypes.byref(buffer), ctypes.byref(size)
    ):
        return ""
    try:
        return str(buffer.value or "")
    finally:
        wtsapi32().WTSFreeMemory(ctypes.cast(buffer, w.LPVOID))


def session_user_sid() -> str | None:
    """The SID of the person signed in to the session this process runs in: the one who
    started the setup, also when an administrator's account gave the rights for it."""
    session = w.DWORD(0)
    if not kernel32().ProcessIdToSessionId(kernel32().GetCurrentProcessId(), ctypes.byref(session)):
        return None
    user = _session_text(session.value, WTS_USER_NAME)
    if not user:
        return None
    domain = _session_text(session.value, WTS_DOMAIN_NAME)
    return account_sid(f"{domain}\\{user}" if domain else user)


# -- local groups ----------------------------------------------------------------------------------


def group_members(group_sid: str) -> set[str]:
    """The SIDs of the members of a local group."""
    buffer, read, total, resume = w.LPVOID(), w.DWORD(0), w.DWORD(0), ULONG_PTR(0)
    status = netapi32().NetLocalGroupGetMembers(
        None, account_name(group_sid), 0, ctypes.byref(buffer), MAX_PREFERRED_LENGTH,
        ctypes.byref(read), ctypes.byref(total), ctypes.byref(resume),
    )  # fmt: skip
    if status not in (0, ERROR_MORE_DATA):
        raise last_error("NetLocalGroupGetMembers", status)
    try:
        entries = ctypes.cast(buffer, ctypes.POINTER(LOCALGROUP_MEMBERS_INFO_0))
        return {sid_text(entries[index].lgrmi0_sid) for index in range(read.value)}
    finally:
        if buffer:
            netapi32().NetApiBufferFree(buffer)


def _change_group(add: bool, group_sid: str, member_sid: str) -> bool:
    group = account_name(group_sid)
    with sid_of(member_sid) as member:
        info = LOCALGROUP_MEMBERS_INFO_0(member)
        call = netapi32().NetLocalGroupAddMembers if add else netapi32().NetLocalGroupDelMembers
        status = call(None, group, 0, ctypes.byref(info), 1)
    if status == 0:
        return True
    if status == (ERROR_MEMBER_IN_ALIAS if add else ERROR_MEMBER_NOT_IN_ALIAS):
        return False
    raise last_error("NetLocalGroupAddMembers" if add else "NetLocalGroupDelMembers", status)


def group_add(group_sid: str, member_sid: str) -> bool:
    """Make the account a member of the local group; False when it was one already."""
    return _change_group(True, group_sid, member_sid)


def group_remove(group_sid: str, member_sid: str) -> bool:
    """Take the account out of the local group; False when it was not in it."""
    return _change_group(False, group_sid, member_sid)


# -- the Service Control Manager -------------------------------------------------------------------


@contextlib.contextmanager
def _service(name: str, access: int) -> Iterator[Any]:
    """The service's handle, or None when there is no such service."""
    manager = advapi32().OpenSCManagerW(None, None, SC_MANAGER_CONNECT)
    if not manager:
        raise last_error("OpenSCManager")
    try:
        handle = advapi32().OpenServiceW(manager, name, access)
        code = ctypes.get_last_error()
        if not handle and code != ERROR_SERVICE_DOES_NOT_EXIST:
            raise last_error(f"OpenService {name}", code)
        try:
            yield handle or None
        finally:
            if handle:
                advapi32().CloseServiceHandle(handle)
    finally:
        advapi32().CloseServiceHandle(manager)


def service_status(name: str) -> tuple[int, int] | None:
    """(state, process id) of a service; None when it is not installed. Any user may ask."""
    with _service(name, SERVICE_QUERY_STATUS) as handle:
        if handle is None:
            return None
        status, needed = SERVICE_STATUS_PROCESS(), w.DWORD(0)
        if not advapi32().QueryServiceStatusEx(
            handle, SC_STATUS_PROCESS_INFO, ctypes.byref(status), ctypes.sizeof(status),
            ctypes.byref(needed),
        ):  # fmt: skip
            raise last_error(f"QueryServiceStatusEx {name}")
        return int(status.dwCurrentState), int(status.dwProcessId)


def start_service(name: str) -> bool:
    """Ask the Service Control Manager to start a service; False where this account may not
    (or it is not installed). True when it starts or runs already."""
    try:
        with _service(name, SERVICE_START) as handle:
            if handle is None:
                return False
            if advapi32().StartServiceW(handle, 0, None):
                return True
            return ctypes.get_last_error() == ERROR_SERVICE_ALREADY_RUNNING
    except OSError:  # no right to open it for starting
        return False


# -- a program with a UAC prompt -------------------------------------------------------------------


class Cancelled(Exception):
    """The person said no to the UAC prompt."""


def run_elevated(program: str, parameters: str, timeout: float | None = None) -> int:
    """Start ``program`` with administrator rights (Windows asks the person first, unless this
    process has them) and wait for it to end; its exit code. Raises Cancelled when the
    person says no, TimeoutError when it runs longer than ``timeout`` seconds."""
    info = SHELLEXECUTEINFOW()
    info.cbSize = ctypes.sizeof(SHELLEXECUTEINFOW)
    info.fMask = SEE_MASK_NOCLOSEPROCESS | SEE_MASK_NOASYNC | SEE_MASK_FLAG_NO_UI
    info.lpVerb, info.lpFile, info.lpParameters = "runas", program, parameters
    info.nShow = SW_HIDE
    if not shell32().ShellExecuteExW(ctypes.byref(info)):
        code = ctypes.get_last_error()
        if code == ERROR_CANCELLED:
            raise Cancelled
        raise last_error(f"ShellExecuteEx {program}", code)
    if not info.hProcess:
        return 0
    try:
        wait = INFINITE if timeout is None else int(timeout * 1000)
        if kernel32().WaitForSingleObject(info.hProcess, wait) == WAIT_TIMEOUT:
            raise TimeoutError(f"{program} did not end within {timeout:.0f} s")
        code = w.DWORD(0)
        if not kernel32().GetExitCodeProcess(info.hProcess, ctypes.byref(code)):
            raise last_error("GetExitCodeProcess")
        return int(code.value)
    finally:
        close(info.hProcess)
