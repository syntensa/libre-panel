"""Install the Windows setup silently, check it, run it, uninstall it.

python packaging/windows/test_installer.py dist/libre-panel-X-windows-x64-setup.exe

It installs for all users with every task, the sensor service among them (CI's runner is an
administrator), and proves that the service runs, that its folder and its pipe let others
only read, that Libre Panel reads through it (as a plain user too, where this runner can
start one), and that the uninstaller stops and removes it.
"""

import ctypes
import json
import os
import re
import subprocess
import sys
import tempfile
import time
import urllib.request
import winreg
from ctypes import wintypes as w
from pathlib import Path

PROGRAM_FILES = Path(os.environ["ProgramFiles"])
APP = PROGRAM_FILES / "Libre Panel"
MENU = Path(os.environ["ProgramData"]) / "Microsoft/Windows/Start Menu/Programs/Libre Panel.lnk"
SMOKE = Path(__file__).resolve().parent.parent / "smoke_test.py"
SERVICE = "LibrePanelSensors"
PIPE = r"\\.\pipe\LibrePanelSensors"
TASKS = "autostart,sensorservice,pawnio,perflogusers"
# SDDL rights by name, as Windows writes them back (a mask without names is hex)
RIGHTS = {
    "GA": 0x10000000, "GR": 0x80000000, "GW": 0x40000000, "GX": 0x20000000,
    "FA": 0x1F01FF, "FR": 0x120089, "FW": 0x120116, "FX": 0x1200A0,
    "CC": 0x1, "DC": 0x2, "LC": 0x4, "SW": 0x8, "RP": 0x10, "WP": 0x20, "DT": 0x40,
    "LO": 0x80, "CR": 0x100, "SD": 0x10000, "RC": 0x20000, "WD": 0x40000, "WO": 0x80000,
}  # fmt: skip
# rights that change something: write or append data (on a pipe: make an instance), delete,
# the DACL, the owner, and the generic and file-wide forms of them
CHANGE = 0x2 | 0x4 | 0x10 | 0x40 | 0x100 | 0x10000 | 0x40000 | 0x80000 | 0x10000000 | 0x40000000


def run_value():
    run = r"Software\Microsoft\Windows\CurrentVersion\Run"
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, run) as key:
            return winreg.QueryValueEx(key, "Libre Panel")[0]
    except FileNotFoundError:
        return None


def check(condition, message):
    if not condition:
        raise SystemExit(f"FAIL: {message}")
    print("ok:", message)


def wait(predicate, seconds):
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        if predicate():
            return True
        time.sleep(0.5)
    return False


# -- what Windows says about the service, its folder and its pipe ----------------------------------


def service_query():
    """sc.exe's answer about the service, and its exit code (1060: there is none)."""
    done = subprocess.run(["sc.exe", "queryex", SERVICE], capture_output=True, text=True)
    return done.returncode, done.stdout


def aces(sddl):
    """{(type, trustee): rights mask} of the DACL in an SDDL string."""
    found = {}
    for kind, rights, trustee in re.findall(r"\((A|D);[^;]*;([^;]*);[^;]*;[^;]*;([^)]*)\)", sddl):
        if rights.lower().startswith("0x"):
            mask = int(rights, 16)
        else:
            mask = sum(RIGHTS[rights[i : i + 2]] for i in range(0, len(rights), 2))
        found[(kind, trustee)] = found.get((kind, trustee), 0) | mask
    return found


def dacl_sddl(path_or_handle, handle_given=False):
    """The DACL of a file or a folder, or (``handle_given``) of what a handle is open on (a
    pipe is a file too), in SDDL."""
    advapi = ctypes.WinDLL("advapi32", use_last_error=True)
    descriptor = w.LPVOID()
    dacl_only = 0x4  # DACL_SECURITY_INFORMATION
    if handle_given:
        advapi.GetSecurityInfo.argtypes = (
            [w.HANDLE, ctypes.c_int, w.DWORD] + [w.LPVOID] * 4 + [ctypes.POINTER(w.LPVOID)]
        )
        status = advapi.GetSecurityInfo(
            path_or_handle, 1, dacl_only, None, None, None, None, ctypes.byref(descriptor)
        )  # SE_FILE_OBJECT
    else:
        advapi.GetNamedSecurityInfoW.argtypes = (
            [w.LPCWSTR, ctypes.c_int, w.DWORD] + [w.LPVOID] * 4 + [ctypes.POINTER(w.LPVOID)]
        )
        status = advapi.GetNamedSecurityInfoW(
            str(path_or_handle), 1, dacl_only, None, None, None, None, ctypes.byref(descriptor)
        )  # SE_FILE_OBJECT
    if status:
        raise OSError(status, "GetSecurityInfo")
    text = w.LPWSTR()
    advapi.ConvertSecurityDescriptorToStringSecurityDescriptorW.argtypes = [
        w.LPVOID, w.DWORD, w.DWORD, ctypes.POINTER(w.LPWSTR), w.LPVOID,
    ]  # fmt: skip
    if not advapi.ConvertSecurityDescriptorToStringSecurityDescriptorW(
        descriptor, 1, dacl_only, ctypes.byref(text), None
    ):
        raise ctypes.WinError(ctypes.get_last_error())
    return text.value


def pipe_dacl():
    """The DACL of the service's pipe, from an instance opened only to read it."""
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.CreateFileW.argtypes = [
        w.LPCWSTR,
        w.DWORD,
        w.DWORD,
        w.LPVOID,
        w.DWORD,
        w.DWORD,
        w.HANDLE,
    ]
    kernel.CreateFileW.restype = w.HANDLE
    handle = kernel.CreateFileW(PIPE, 0x20000 | 0x80, 0, None, 3, 0, None)  # READ_CONTROL
    if not handle or handle == ctypes.c_void_p(-1).value:
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        return dacl_sddl(handle, handle_given=True)
    finally:
        kernel.CloseHandle(w.HANDLE(handle))


# -- a plain user ----------------------------------------------------------------------------------


def token_groups():
    """The SIDs of this process's token."""
    advapi = ctypes.WinDLL("advapi32", use_last_error=True)
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.GetCurrentProcess.restype = w.HANDLE
    token = w.HANDLE()
    advapi.OpenProcessToken.argtypes = [w.HANDLE, w.DWORD, ctypes.POINTER(w.HANDLE)]
    advapi.OpenProcessToken(kernel.GetCurrentProcess(), 0x8, ctypes.byref(token))
    size = w.DWORD()
    advapi.GetTokenInformation.argtypes = [w.HANDLE, ctypes.c_int, w.LPVOID, w.DWORD, w.LPDWORD]
    advapi.GetTokenInformation(token, 2, None, 0, ctypes.byref(size))  # TokenGroups
    buffer = ctypes.create_string_buffer(size.value)
    advapi.GetTokenInformation(token, 2, buffer, size, ctypes.byref(size))
    count = ctypes.c_uint32.from_buffer(buffer).value
    offset = ctypes.sizeof(ctypes.c_void_p)  # the count, padded to a pointer
    advapi.ConvertSidToStringSidW.argtypes = [w.LPVOID, ctypes.POINTER(w.LPWSTR)]
    sids = []
    for index in range(count):
        sid = ctypes.c_void_p.from_buffer(
            buffer, offset + index * 2 * ctypes.sizeof(ctypes.c_void_p)
        )
        text = w.LPWSTR()
        advapi.ConvertSidToStringSidW(sid, ctypes.byref(text))
        sids.append(text.value)
    return sids


class STARTUPINFO(ctypes.Structure):
    _fields_ = [
        ("cb", w.DWORD), ("lpReserved", w.LPWSTR), ("lpDesktop", w.LPWSTR),
        ("lpTitle", w.LPWSTR), ("dwX", w.DWORD), ("dwY", w.DWORD), ("dwXSize", w.DWORD),
        ("dwYSize", w.DWORD), ("dwXCountChars", w.DWORD), ("dwYCountChars", w.DWORD),
        ("dwFillAttribute", w.DWORD), ("dwFlags", w.DWORD), ("wShowWindow", w.WORD),
        ("cbReserved2", w.WORD), ("lpReserved2", w.LPVOID), ("hStdInput", w.HANDLE),
        ("hStdOutput", w.HANDLE), ("hStdError", w.HANDLE),
    ]  # fmt: skip


class PROCESS_INFORMATION(ctypes.Structure):
    _fields_ = [
        ("hProcess", w.HANDLE), ("hThread", w.HANDLE),
        ("dwProcessId", w.DWORD), ("dwThreadId", w.DWORD),
    ]  # fmt: skip


def run_as_plain_user(command, output):
    """Run ``command`` with this account's token made that of a plain user (SAFER's "normal
    user": the Administrators group only denies, medium integrity), its output into the file
    ``output``; its exit code. OSError where Windows does not allow that here."""
    advapi = ctypes.WinDLL("advapi32", use_last_error=True)
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    level, token = w.HANDLE(), w.HANDLE()
    advapi.SaferCreateLevel.argtypes = [
        w.DWORD, w.DWORD, w.DWORD, ctypes.POINTER(w.HANDLE), w.LPVOID,
    ]  # fmt: skip
    advapi.SaferComputeTokenFromLevel.argtypes = [
        w.HANDLE, w.HANDLE, ctypes.POINTER(w.HANDLE), w.DWORD, w.LPVOID,
    ]  # fmt: skip
    # SAFER_SCOPEID_USER, SAFER_LEVELID_NORMALUSER, SAFER_LEVEL_OPEN
    if not advapi.SaferCreateLevel(2, 0x20000, 1, ctypes.byref(level), None):
        raise ctypes.WinError(ctypes.get_last_error())
    if not advapi.SaferComputeTokenFromLevel(level, None, ctypes.byref(token), 0, None):
        raise ctypes.WinError(ctypes.get_last_error())
    medium = w.LPVOID()  # the integrity of a person's own programs
    advapi.ConvertStringSidToSidW.argtypes = [w.LPCWSTR, ctypes.POINTER(w.LPVOID)]
    advapi.GetLengthSid.argtypes = [w.LPVOID]
    if advapi.ConvertStringSidToSidW("S-1-16-8192", ctypes.byref(medium)):

        class MandatoryLabel(ctypes.Structure):
            _fields_ = [("Sid", w.LPVOID), ("Attributes", w.DWORD)]

        label = MandatoryLabel(medium, 0x20)  # SE_GROUP_INTEGRITY
        size = ctypes.sizeof(label) + advapi.GetLengthSid(medium)
        advapi.SetTokenInformation.argtypes = [w.HANDLE, ctypes.c_int, w.LPVOID, w.DWORD]
        if not advapi.SetTokenInformation(token, 25, ctypes.byref(label), size):
            print(f"note: the plain user keeps this integrity ({ctypes.get_last_error()})")

    class SecurityAttributes(ctypes.Structure):
        _fields_ = [
            ("nLength", w.DWORD), ("lpSecurityDescriptor", w.LPVOID), ("bInheritHandle", w.BOOL),
        ]  # fmt: skip

    inheritable = SecurityAttributes(ctypes.sizeof(SecurityAttributes), None, True)
    kernel.CreateFileW.argtypes = [
        w.LPCWSTR, w.DWORD, w.DWORD, w.LPVOID, w.DWORD, w.DWORD, w.HANDLE,
    ]  # fmt: skip
    kernel.CreateFileW.restype = w.HANDLE
    # GENERIC_WRITE, shared, CREATE_ALWAYS, FILE_ATTRIBUTE_NORMAL
    out = kernel.CreateFileW(str(output), 0x40000000, 3, ctypes.byref(inheritable), 2, 0x80, None)
    if not out or out == ctypes.c_void_p(-1).value:
        raise ctypes.WinError(ctypes.get_last_error())
    info = STARTUPINFO(cb=ctypes.sizeof(STARTUPINFO), dwFlags=0x100)  # STARTF_USESTDHANDLES
    info.hStdOutput = info.hStdError = out
    process = PROCESS_INFORMATION()
    advapi.CreateProcessAsUserW.argtypes = [
        w.HANDLE, w.LPCWSTR, w.LPWSTR, w.LPVOID, w.LPVOID, w.BOOL, w.DWORD, w.LPVOID,
        w.LPCWSTR, ctypes.POINTER(STARTUPINFO), ctypes.POINTER(PROCESS_INFORMATION),
    ]  # fmt: skip
    line = ctypes.create_unicode_buffer(subprocess.list2cmdline(command))
    created = advapi.CreateProcessAsUserW(
        token, None, line, None, None, True, 0x08000000, None, None,  # CREATE_NO_WINDOW
        ctypes.byref(info), ctypes.byref(process),
    )  # fmt: skip
    error = ctypes.get_last_error()
    kernel.CloseHandle.argtypes = [w.HANDLE]
    kernel.CloseHandle(out)
    if not created:
        raise ctypes.WinError(error)
    kernel.WaitForSingleObject.argtypes = [w.HANDLE, w.DWORD]
    kernel.WaitForSingleObject(process.hProcess, 180_000)
    code = w.DWORD()
    kernel.GetExitCodeProcess.argtypes = [w.HANDLE, w.LPDWORD]
    kernel.GetExitCodeProcess(process.hProcess, ctypes.byref(code))
    kernel.CloseHandle(process.hProcess)
    kernel.CloseHandle(process.hThread)
    return code.value


# -- the steps -------------------------------------------------------------------------------------


def check_sensor_service():
    """The setup installed and started the service, from a folder and with a pipe that let
    the others only read; Libre Panel reads through it."""
    code, said = service_query()
    print(said)
    check(code == 0 and re.search(r"STATE\s*:\s*4", said), "the sensor service runs")
    status = subprocess.run(
        [str(APP / "libre-panel.exe"), "system-setup", "--status"],
        capture_output=True,
        text=True,
        timeout=60,
    )
    print(status.stdout, status.stderr)
    state = json.loads(status.stdout)
    program = f'"{APP / "sensors" / "libre-panel-sensors.exe"}"'
    check(state["service"]["program"].lower() == program.lower(), f"it runs {program}")
    check(state["perf_log_users"] is True, "the person who installed is in Performance Log Users")
    if state["pawnio_installer"]:
        check(state["pawnio"] is not None, f"PawnIO is installed ({state['pawnio']})")
    else:
        print("note: this download carries no PawnIO installer; PawnIO not checked")

    folder = dacl_sddl(APP / "sensors")
    print("the service's folder:", folder)
    rights = aces(folder)
    check(folder.startswith("D:P"), "the service's folder inherits nothing")
    others = {
        who: mask for (kind, who), mask in rights.items() if kind == "A" and who not in ("SY", "BA")
    }
    check(others and all(not mask & CHANGE for mask in others.values()), "others may only read it")

    pipe = pipe_dacl()
    print("the service's pipe:", pipe)
    rights = aces(pipe)
    check(pipe.startswith("D:P") and ("D", "NU") in rights, "network logons are refused first")
    interactive = rights.get(("A", "IU"), 0)
    check(interactive & 0x3 == 0x3 and not interactive & ~0x120083, "people signed in may read")
    check(
        all(not mask & CHANGE & ~0x2 for (kind, who), mask in rights.items() if who == "IU"),
        "... and neither make an instance of the pipe nor change its rights",
    )

    lhm = subprocess.run(
        [str(APP / "libre-panel.exe"), "sensors", "--check-service"],
        capture_output=True,
        text=True,
        timeout=180,
    )
    print(lhm.stdout, lhm.stderr)
    check(lhm.returncode == 0, "Libre Panel reads the sensors through the service")
    check("read through the sensor service" in lhm.stdout, "... and says so")

    if "S-1-5-4" not in token_groups():  # INTERACTIVE
        print("note: this runner's logon is not interactive, and the pipe admits interactive")
        print("      logons only: the check as a plain user is left to a real desktop")
        return
    output = Path(tempfile.gettempdir()) / "libre-panel-plain-user.txt"
    try:
        code = run_as_plain_user(
            [str(APP / "libre-panel.exe"), "sensors", "--check-service"], output
        )
    except OSError as exc:
        print(f"note: no plain user's process can be started here ({exc}): not checked")
        return
    said = output.read_text(encoding="utf-8", errors="replace")
    print(said)
    check(code == 0 and "read through the sensor service" in said, "a plain user reads too")


def main(setup):
    log = Path(tempfile.gettempdir()) / "libre-panel-setup.log"
    subprocess.run(
        [
            setup,
            "/VERYSILENT",
            "/SUPPRESSMSGBOXES",
            "/NORESTART",
            "/ALLUSERS",
            f"/TASKS={TASKS}",
            f"/LOG={log}",
        ],
        check=True,
        timeout=900,
    )
    check((APP / "LibrePanel.exe").is_file(), "LibrePanel.exe installed")
    check((APP / "libre-panel.exe").is_file(), "libre-panel.exe installed")
    check((APP / "sensors" / "libre-panel-sensors.exe").is_file(), "the sensor service installed")
    check(MENU.is_file(), "start menu entry")
    value = run_value() or ""
    check("LibrePanel.exe" in value and "tray --background" in value, f"autostart entry: {value}")

    # It carries LibreHardwareMonitor and loads it: no separate program for the GPU,
    # temperatures, power and fans (no administrator rights needed for this check).
    lhm = subprocess.run(
        [str(APP / "libre-panel.exe"), "sensors", "--check-lhm"],
        capture_output=True,
        text=True,
        timeout=180,
    )
    print(lhm.stdout, lhm.stderr)
    check(lhm.returncode == 0, "the installed program loads its LibreHardwareMonitor libraries")

    check_sensor_service()

    # The installed program works as autostart would start it.
    subprocess.run(
        [sys.executable, str(SMOKE), str(APP / "LibrePanel.exe"), "--background"], check=True
    )

    # Uninstalling quits a running Libre Panel and removes everything it added.
    with tempfile.TemporaryDirectory() as home:
        os.environ["LIBRE_PANEL_HOME"] = home
        Path(home, "config.toml").write_text(
            '[device]\ndriver = "virtual"\noutput = "frame.png"\n', encoding="utf-8"
        )
        running = subprocess.Popen([str(APP / "LibrePanel.exe"), "--background"])
        info = Path(home, "instance.json")
        check(wait(info.exists, 60), "background app started")
        url = json.loads(info.read_text())["editor"]
        # the server answers a request with the access token the app wrote for its user
        token = json.loads(Path(home, "private", "editor.json").read_text())["token"]
        request = urllib.request.Request(url + "api/app", headers={"X-Libre-Panel": token})
        with urllib.request.urlopen(request, timeout=10) as response:
            check(json.loads(response.read())["available"], "editor answers")

        subprocess.run(
            [str(APP / "unins000.exe"), "/VERYSILENT", "/SUPPRESSMSGBOXES", "/NORESTART"]
        )
        check(wait(lambda: running.poll() is not None, 60), "uninstaller quit the running app")
        check(wait(lambda: not (APP / "LibrePanel.exe").exists(), 120), "program files removed")
    check(not MENU.exists(), "start menu entry removed")
    check(run_value() is None, "autostart entry removed")
    check(wait(lambda: service_query()[0] == 1060, 60), "the sensor service is removed")
    check(wait(lambda: not (APP / "sensors").exists(), 60), "and its files")
    print("OK")


if __name__ == "__main__":
    main(sys.argv[1])
