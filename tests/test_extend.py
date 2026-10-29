"""A monitor of the panel's own (extend.py): the Virtual Display Driver's settings with the
panel's size among its resolutions, what is missing where; on X11 the room right of the
monitors, made with xrandr and given back."""

import base64
import hashlib
import io
import re
import zipfile
from pathlib import Path

import pytest

from libre_panel import capture, extend

SETTINGS = """<?xml version='1.0' encoding='utf-8'?>
<vdd_settings>
    <monitors>
        <count>1</count>
    </monitors>
    <resolutions>
        <resolution>
            <width>1920</width>
            <height>1080</height>
            <refresh_rate>60</refresh_rate>
        </resolution>
    </resolutions>
    <logging>
        <logging>false</logging>
    </logging>
</vdd_settings>
"""


def test_the_drivers_settings_get_the_panels_size_once():
    assert not extend.vdd_has_resolution((1920, 720), SETTINGS)
    changed = extend.with_resolution(SETTINGS, (1920, 720))
    assert extend.vdd_has_resolution((1920, 720), changed)
    assert extend.vdd_has_resolution((1920, 1080), changed)  # (its own kept)
    assert extend.with_resolution(changed, (1920, 720)) == changed  # (once)
    assert changed.index("<width>1920</width>\n            <height>720</height>") < changed.index(
        "</resolutions>"
    )
    assert changed.count("<resolution>") == 2 and "<logging>false</logging>" in changed
    bare = "<vdd_settings>\n    <monitors><count>1</count></monitors>\n</vdd_settings>\n"
    assert extend.vdd_has_resolution((480, 1920), extend.with_resolution(bare, (480, 1920)))
    with pytest.raises(extend.ExtendError):
        extend.with_resolution("<html/>", (1920, 720))


def test_what_is_missing_where(monkeypatch):
    monkeypatch.setattr(extend, "vdd_installed", lambda: False)
    assert extend.state((1920, 720), "windows") == {"how": "vdd", "ready": False,
                                                    "missing": "driver"}  # fmt: skip
    monkeypatch.setattr(extend, "vdd_installed", lambda: True)
    monkeypatch.setattr(extend, "vdd_has_resolution", lambda size, text=None: False)
    assert extend.state((1920, 720), "windows")["missing"] == "resolution"
    monkeypatch.setattr(extend, "vdd_has_resolution", lambda size, text=None: True)
    assert extend.state((1920, 720), "windows") == {"how": "vdd", "ready": True, "missing": ""}
    monkeypatch.setattr(extend.shutil, "which", lambda name: None)
    assert extend.state((1920, 720), "x11")["missing"] == "xrandr"
    assert extend.state((1920, 720), "other") == {"how": "", "ready": False, "missing": ""}
    if extend.sys.platform != "win32":  # (the driver is Windows' alone)
        with pytest.raises(extend.ExtendError):
            extend.setup((1920, 720))


# -- Windows: setting the driver up ------------------------------------------------------------


def _package(folder, settings=SETTINGS):
    """A made-up package of the driver alone, as its release has it."""
    path = folder / "VirtualDisplayDriver-x86.Driver.Only.zip"
    with zipfile.ZipFile(path, "w") as archive:
        for name in ("MttVDD.inf", "MttVDD.dll", "mttvdd.cat"):
            archive.writestr(f"VirtualDisplayDriver/{name}", b"x")
        archive.writestr("VirtualDisplayDriver/vdd_settings.xml", settings.encode("utf-8"))
    return path


def test_a_download_is_kept_only_when_it_is_the_file_it_should_be(monkeypatch, tmp_path):
    data = b"the driver's package"
    good = hashlib.sha256(data).hexdigest()

    class Answer(io.BytesIO):
        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

    asked = []

    def urlopen(request, timeout):
        asked.append((request.full_url, request.get_header("User-agent"), timeout))
        return Answer(data)

    monkeypatch.setattr(extend.urllib.request, "urlopen", urlopen)
    path = extend.download("https://example.org/releases/package.zip", good, tmp_path)
    assert path == tmp_path / "package.zip" and path.read_bytes() == data
    assert asked[0][1].startswith("libre-panel/") and asked[0][2] == extend.DOWNLOAD_TIMEOUT_S
    with pytest.raises(extend.ExtendError, match="not the file it should be"):
        extend.download("https://example.org/other.zip", "0" * 64, tmp_path)
    assert not (tmp_path / "other.zip").exists()
    monkeypatch.setattr(extend, "DOWNLOAD_LIMIT", 4)  # (too large: not read to its end)
    with pytest.raises(extend.ExtendError):
        extend.download("https://example.org/large.zip", good, tmp_path)

    def offline(request, timeout):
        raise OSError("no network")

    monkeypatch.setattr(extend.urllib.request, "urlopen", offline)
    with pytest.raises(extend.ExtendError, match="could not be downloaded .no network"):
        extend.download("https://example.org/package.zip", good, tmp_path)


def test_the_packages_are_the_releases_files_and_known_by_their_sha256():
    for machine, (url, sha) in extend.VDD_PACKAGES.items():
        assert url.startswith("https://github.com/VirtualDrivers/Virtual-Display-Driver/")
        assert re.fullmatch(r"[0-9a-f]{64}", sha) and machine in extend.NEFCON_FOLDERS
    url, sha = extend.NEFCON
    assert url.startswith("https://github.com/nefarius/nefcon/")
    assert re.fullmatch(r"[0-9a-f]{64}", sha)


def test_the_drivers_own_settings_come_from_its_package(tmp_path):
    assert extend.package_settings(_package(tmp_path)) == SETTINGS


def test_the_machine_is_windows_own(monkeypatch):
    if extend.sys.platform == "win32":
        assert extend.windows_machine() in ("AMD64", "ARM64")
        return
    for seen, machine in (("x86_64", "AMD64"), ("AMD64", "AMD64"), ("aarch64", "ARM64"),
                          ("ARM64", "ARM64")):  # fmt: skip
        monkeypatch.setattr(extend.platform, "machine", lambda seen=seen: seen)
        assert extend.windows_machine() == machine


def test_the_install_script_checks_what_it_runs_and_says_how_it_went(tmp_path):
    odd = tmp_path / "Jo's files"
    script = extend.install_script(
        odd / "nefcon.zip", "a" * 64, "x64/nefconc.exe", odd / "driver.zip", "b" * 64,
        odd / "vdd_settings.xml", "c" * 64, Path(r"C:\VirtualDisplayDriver"), odd / "said.txt",
    )  # fmt: skip
    quoted = extend._ps_quote(str(odd))[:-1]  # (a quote in a path is doubled in its string)
    # (the three files it reads, and where it says how it went: done, or what failed)
    assert "''" in quoted and script.count(quoted) == 5
    for sha in ("a" * 64, "b" * 64, "c" * 64):
        assert script.count(f"'{sha}'") == 1
    assert "'x64/nefconc.exe'" in script and r"'Root\MttVDD'" in script
    assert "install $inf 'Root\\MttVDD' --no-duplicates --remove-duplicates" in script
    assert "'TrustedPublisher', 'LocalMachine'" in script and "CertificateAuthority" in script
    assert "/inheritance:r" in script and r"'C:\VirtualDisplayDriver'" in script
    assert script.rstrip().endswith("exit $code")
    assert script.count("{") == script.count("}") and script.count("(") == script.count(")")
    assert script.count("'") % 2 == 0


@pytest.mark.skipif(extend.sys.platform != "win32", reason="Windows' own PowerShell runs it")
def test_windows_powershell_runs_the_install_script_up_to_the_drivers_signer(tmp_path):
    """The script as Windows PowerShell runs it, started as the setup starts it (not as the
    administrator): it reads and checks the files, takes NefCon and the driver from their
    packages into a folder of its own, and fails at the made-up catalog, before it trusts a
    signer, writes the settings or installs anything, saying why; its folder is gone."""
    import os
    import subprocess

    odd = tmp_path / "Jo's files"
    odd.mkdir()
    nefcon = odd / "nefcon.zip"
    with zipfile.ZipFile(nefcon, "w") as archive:
        archive.writestr("x64/nefconc.exe", b"not a program")
    package = _package(odd)
    settings = odd / "vdd_settings.xml"
    settings.write_text(SETTINGS, encoding="utf-8")
    target, said = tmp_path / "VirtualDisplayDriver", odd / "said.txt"
    sha = {path: hashlib.sha256(path.read_bytes()).hexdigest()
           for path in (nefcon, package, settings)}  # fmt: skip
    data = Path(os.environ["ProgramData"])

    def run(package_sha):
        before = set(data.glob("LibrePanel-vdd-*"))
        script = extend.install_script(
            nefcon, sha[nefcon], "x64/nefconc.exe", package, package_sha, settings,
            sha[settings], target, said,
        )  # fmt: skip
        program, parameters = extend.powershell(script)
        done = subprocess.run(f'"{program}" {parameters}', capture_output=True, timeout=120)
        told = said.read_text(encoding="utf-8")
        assert set(data.glob("LibrePanel-vdd-*")) == before, told  # (its folder is gone)
        return done.returncode, told

    code, told = run("0" * 64)  # a package that is not the one downloaded: nothing is done
    assert code == 1 and told.startswith("error: ") and "is not the file that was" in told
    code, told = run(sha[package])
    assert code == 1 and told.startswith("error: "), told
    for wrong in ("is not the file", "is not in its package", "Unable to find type",
                  "Cannot find an overload", "Method invocation failed", "not recognized",
                  "Cannot convert", "ParserError"):  # fmt: skip
        assert wrong not in told, told
    assert not target.exists()  # the signer comes before the settings


def test_installing_keeps_the_drivers_settings_and_adds_the_panels_size(monkeypatch, tmp_path):
    folder = tmp_path / "VirtualDisplayDriver"
    monkeypatch.setattr(extend, "vdd_folder", lambda: folder)
    monkeypatch.setattr(extend, "windows_machine", lambda: "AMD64")
    made = tmp_path / "made"
    made.mkdir()
    package = _package(made)
    nefcon = made / "nefcon.zip"
    nefcon.write_bytes(b"nefcon")
    monkeypatch.setattr(extend, "download",
                        lambda url, sha, into: package if "Virtual" in url else nefcon)  # fmt: skip
    runs = []

    def run(program, parameters, timeout):
        assert program.replace("\\", "/").endswith("WindowsPowerShell/v1.0/powershell.exe")
        assert "-ExecutionPolicy Bypass" in parameters and "-NoProfile" in parameters
        encoded = parameters.rsplit(" ", 1)[-1]
        script = base64.b64decode(encoded).decode("utf-16-le")
        settings = Path(re.search(r"\$settings = Read-Checked '([^']*)' '([0-9a-f]+)'",
                                  script).group(1))  # fmt: skip
        runs.append((script, settings.read_text(encoding="utf-8")))
        said = Path(re.findall(r"WriteAllText\('([^']*)'", script)[0])
        said.write_text("error: the store is closed" if code[0] else "0", encoding="utf-8")
        return code[0]

    code = [0]
    monkeypatch.setattr(extend, "_run_elevated", run)
    assert extend._vdd_install((1920, 720)) == 0
    script, settings = runs[-1]
    assert extend.vdd_has_resolution((1920, 720), settings)  # the package's own, and the panel's
    assert extend.vdd_has_resolution((1920, 1080), settings)
    assert f"'{hashlib.sha256(settings.encode()).hexdigest()}'" in script
    assert f"'{extend.VDD_PACKAGES['AMD64'][1]}'" in script and "'x64/nefconc.exe'" in script
    # settings that are there already are kept, with the panel's size
    folder.mkdir()
    (folder / "vdd_settings.xml").write_text(SETTINGS.replace("1080", "1200"), encoding="utf-8")
    extend._vdd_install((480, 1920))
    assert "<height>1200</height>" in runs[-1][1] and extend.vdd_has_resolution(
        (480, 1920), runs[-1][1]
    )
    code[0] = 1
    with pytest.raises(extend.ExtendError, match=r"not installed \(the store is closed\)"):
        extend._vdd_install((1920, 720))
    code[0] = extend.REBOOT_REQUIRED
    assert extend._vdd_install((1920, 720)) == extend.REBOOT_REQUIRED


def test_setting_up_installs_the_driver_once_and_gives_it_the_panels_size(monkeypatch):
    monkeypatch.setattr(extend.sys, "platform", "win32")
    did = []
    there = {"driver": False, "size": False}
    monkeypatch.setattr(extend, "vdd_installed", lambda: there["driver"])
    monkeypatch.setattr(extend, "vdd_has_resolution", lambda size, text=None: there["size"])

    def install(size):
        did.append(("install", size))
        there.update(driver=True, size=True)  # (its settings bring the panel's size)
        return 0

    def reload(tries=1):
        did.append("reload")
        raise extend.ExtendError("the driver did not answer")  # (just started: not yet)

    monkeypatch.setattr(extend, "_vdd_install", install)
    monkeypatch.setattr(extend, "_vdd_write_resolution",
                        lambda size: (did.append("size"), there.update(size=True)))  # fmt: skip
    monkeypatch.setattr(extend, "_vdd_reload", reload)
    monkeypatch.setattr(extend, "_vdd_detach", lambda: did.append("detach"))
    monkeypatch.setattr(extend.time, "sleep", lambda s: None)
    assert extend.setup((1920, 720)) == "driver"
    assert did == [("install", (1920, 720)), "reload", "detach"]
    # installed already, without the panel's size: only that, and the driver must answer
    did.clear()
    there["size"] = False
    with pytest.raises(extend.ExtendError, match="did not answer"):
        extend.setup((1920, 720))
    monkeypatch.setattr(extend, "_vdd_reload", lambda tries=1: did.append("reload"))
    did.clear()
    there["size"] = False
    assert extend.setup((1920, 720)) == "resolution"
    assert did == ["size", "reload", "detach"]
    # a driver Windows shows no display of after its install: said, not hidden
    there.update(driver=False, size=False)
    monkeypatch.setattr(extend, "_vdd_install", lambda size: 0)
    monkeypatch.setattr(extend, "_wait_for", lambda ready, seconds: False)
    with pytest.raises(extend.ExtendError, match="restart Windows"):
        extend.setup((1920, 720))
    monkeypatch.setattr(extend, "_wait_for", lambda ready, seconds: True)
    monkeypatch.setattr(extend, "_vdd_install", lambda size: extend.REBOOT_REQUIRED)
    there.update(size=True)
    assert extend.setup((1920, 720)) == "driver (Windows wants a restart)"


def test_x11_makes_room_right_of_the_monitors_and_gives_it_back(monkeypatch):
    monitors = [capture.Monitor("DP-1", "DP-1", 0, 0, 2560, 1440, True),
                capture.Monitor("HDMI-1", "HDMI-1", 2560, 200, 1920, 1080)]  # fmt: skip
    assert extend.x11_plan((4480, 1440), monitors, (1920, 720)) == ((6400, 1440), (4480, 0))
    calls = []
    listed = ["Monitors: 2\n 0: +*DP-1 2560/597x1440/336+0+0  DP-1\n"
              " 1: +HDMI-1 1920/527x1080/296+2560+200  HDMI-1\n"]  # fmt: skip

    def xrandr(*args):
        calls.append(args)
        if args == ("--listmonitors",):
            return listed[0]
        if args == ("-q",):
            return "Screen 0: minimum 8 x 8, current 4480 x 1440, maximum 32767 x 32767\n"
        return ""

    monkeypatch.setattr(extend, "_xrandr", xrandr)
    monkeypatch.setattr(capture, "monitors", lambda where=None: monitors)
    extend.attach((1920, 720), "x11")
    assert ("--fb", "6400x1440") in calls
    assert ("--setmonitor", "LibrePanel", "1920/508x720/191+4480+0", "none") in calls
    listed[0] += " 2: LibrePanel 1920/508x720/191+4480+0  none\n"
    calls.clear()
    extend.detach("x11")
    assert calls[1:] == [("--delmonitor", "LibrePanel"), ("--fb", "4480x1440")]
    # where the screen is already large enough, it stays as it is
    calls.clear()
    listed[0] = listed[0].split(" 2:")[0]
    monkeypatch.setattr(extend, "_xrandr", lambda *a: calls.append(a) or (
        "Screen 0: current 8000 x 2000" if a == ("-q",) else listed[0]))  # fmt: skip
    extend.attach((1920, 720), "x11")
    assert not any(call[0] == "--fb" for call in calls)


def test_a_failing_x11_says_why(monkeypatch):
    def xrandr(*args):
        if args[0] == "--fb":
            raise OSError("xrandr: screen cannot be larger than 1600x900")
        return "Screen 0: current 1600 x 900" if args == ("-q",) else "Monitors: 0\n"

    monkeypatch.setattr(extend, "_xrandr", xrandr)
    monkeypatch.setattr(capture, "monitors", lambda where=None: [
        capture.Monitor("screen", "screen", 0, 0, 1600, 900, True)])  # fmt: skip
    with pytest.raises(extend.ExtendError, match="cannot be larger"):
        extend.attach((1920, 720), "x11")
