"""Whether a newer Libre Panel is out (editor/updates.py): asked only where it is switched on,
once a day, kept in updates.json, and what Start is told of it; and "Update to": the release's
file for this install, fetched with its progress, checked, and put in place on a second click."""

import base64
import hashlib
import io
import json
import os
import threading
import urllib.error
import urllib.request
import zipfile
from pathlib import Path, PureWindowsPath

import pytest

from libre_panel import __version__
from libre_panel.editor import updates
from libre_panel.editor.updates import (
    RELEASE_FILES,
    WAIT_THEN_START,
    Install,
    UpdateError,
    Updates,
    install_kind,
    move_autostart,
    newer,
    open_https,
    restart_command,
    version_key,
)

PAGE = "https://github.com/syntensa/libre-panel/releases/tag/v9.1.0"


def answer(tag="v9.1.0", body="The volume on every screen.\n\n**New**\n- more"):
    """GitHub's answer on the latest release, as much of it as is read."""
    return {
        "tag_name": tag,
        "html_url": PAGE,
        "published_at": "2026-11-01T10:00:00Z",
        "body": body,
        "assets": [
            {
                "name": f"libre-panel-{tag[1:]}-windows-x64-setup.exe",
                "browser_download_url": f"https://github.com/x/setup-{tag}.exe",
            },
            {
                "name": f"Libre_Panel-{tag[1:]}-x86_64.AppImage",
                "browser_download_url": f"https://github.com/x/{tag}.AppImage",
            },
            {"name": "evil-windows-x64-portable.zip", "browser_download_url": "https://evil/x.zip"},
        ],
    }


class Clock:
    def __init__(self):
        self.now = 1_000_000.0

    def __call__(self):
        return self.now


@pytest.fixture
def rig(tmp_path):
    calls = []
    clock = Clock()

    def fetch():
        calls.append(clock.now)
        if isinstance(rig.answer, Exception):
            raise rig.answer
        return rig.answer

    class rig:  # noqa: N801
        answer = answer()

    rig.calls, rig.clock = calls, clock
    rig.path = tmp_path / "updates.json"
    rig.updates = Updates(tmp_path / "config.toml", fetch=fetch, clock=clock, kind=lambda: "setup")
    return rig


def test_versions_compare_by_their_numbers():
    assert version_key("v0.6.1") == (0, 6, 1, 0, 3, 0) and version_key("0.7")[:4] == (0, 7, 0, 0)
    assert version_key("v0.6.8.1")[:4] == (0, 6, 8, 1) and newer("0.6.8.1", "0.6.8")  # a hotfix
    # a pre-release comes before its release, and after the one before
    assert newer("0.7.0", "0.7.0rc1") and newer("0.7.0rc2", "0.7.0rc1")
    assert newer("0.7.0rc1", "0.6.9") and newer("0.7.0rc1", "0.7.0b3")
    assert not newer("0.7.0rc1", "0.7.0")
    assert newer("0.6.9", "0.6.8.1") and not newer("0.6.8", "0.6.8.1")
    assert version_key("latest") is None and version_key("v1.2.3-rc1") is None
    assert newer("0.10.0", "0.9.9") and newer("v1.0", "0.6.1")
    assert not newer("0.6.1", "0.6.1") and not newer("0.6.0", "0.6.1") and not newer("x", "0.6")


def test_nothing_is_asked_unless_it_is_switched_on(rig):
    for check in (None, False):
        state = rig.updates.state(check)
        assert state["check"] is check and state["latest"] is None and not state["newer"]
        assert state["current"] == __version__ and state["kind"] == "setup"
    assert rig.calls == [] and not rig.path.exists()


def test_switched_on_github_is_asked_once_a_day_and_the_answer_kept(rig):
    state = rig.updates.state(True)
    assert rig.calls == [rig.clock.now]
    assert state["newer"] and state["error"] == "" and state["asked"] == rig.clock.now
    assert state["latest"] == {
        "version": "9.1.0",
        "page": PAGE,
        "download": "https://github.com/x/setup-v9.1.0.exe",  # the setup's: it came with it
        "notes": "The volume on every screen.",  # the first paragraph
        "published": "2026-11-01T10:00:00Z",
        "asset": None,  # not among the release's own files: no "Update to"
        "sums": "",
    }
    kept = json.loads(rig.path.read_text())
    assert kept["latest"]["version"] == "9.1.0"
    rig.clock.now += 23 * 3600
    assert rig.updates.state(True)["newer"] and len(rig.calls) == 1  # from updates.json
    rig.clock.now += 2 * 3600
    rig.answer = answer("v9.2.0")
    assert rig.updates.state(True)["latest"]["version"] == "9.2.0" and len(rig.calls) == 2
    # "Look now" asks at once; switched off, the answer kept is not shown
    assert rig.updates.state(True, now=True) and len(rig.calls) == 3
    assert rig.updates.state(False)["latest"] is None


def test_the_running_version_or_an_older_one_is_no_news(rig):
    rig.answer = answer(f"v{__version__}")
    assert not rig.updates.state(True)["newer"]
    rig.answer = answer("v0.0.1")
    assert not rig.updates.state(True, now=True)["newer"]


def test_no_answer_is_said_and_asked_again_an_hour_later(rig):
    rig.answer = OSError("network is unreachable")
    state = rig.updates.state(True)
    assert state["error"] == "network is unreachable" and state["latest"] is None
    rig.clock.now += 1800
    rig.updates.state(True)
    assert len(rig.calls) == 1
    rig.clock.now += 1800
    rig.answer = answer()
    state = rig.updates.state(True)
    assert len(rig.calls) == 2 and state["newer"] and state["error"] == ""


def test_an_answer_that_names_no_release_is_not_taken(rig):
    rig.answer = {"message": "Not Found"}
    state = rig.updates.state(True)
    assert state["latest"] is None and state["error"]
    rig.answer = {**answer(), "html_url": "https://evil.example/release"}
    assert rig.updates.state(True, now=True)["latest"] is None


def test_later_hides_that_version_not_the_next(rig):
    rig.updates.state(True)
    assert rig.updates.later("9.1.0", True)["later"] == "9.1.0"
    assert json.loads(rig.path.read_text())["later"] == "9.1.0"
    with pytest.raises(ValueError):
        rig.updates.later("soon", True)


def test_the_download_is_the_one_for_this_kind_of_install(rig, tmp_path):
    for kind, wanted in (
        ("appimage", "https://github.com/x/v9.1.0.AppImage"),
        ("portable", ""),  # not on GitHub: not offered
        ("python", ""),
    ):
        config = tmp_path / kind / "config.toml"
        mine = Updates(config, fetch=lambda: answer(), kind=lambda k=kind: k)
        assert mine.state(True)["latest"]["download"] == wanted


def test_install_kind_from_how_it_runs(monkeypatch, tmp_path):
    monkeypatch.delattr(updates.sys, "frozen", raising=False)
    assert install_kind() == "python"
    monkeypatch.setattr(updates.sys, "frozen", True, raising=False)
    monkeypatch.setattr(updates.sys, "platform", "win32")
    monkeypatch.setattr(updates.sys, "executable", str(tmp_path / "LibrePanel.exe"))
    assert install_kind() == "portable"
    (tmp_path / "unins000.exe").write_bytes(b"")
    assert install_kind() == "setup"
    monkeypatch.setattr(updates.sys, "platform", "linux")
    monkeypatch.setenv("APPIMAGE", "/home/someone/Libre_Panel.AppImage")
    assert install_kind() == "appimage"
    monkeypatch.delenv("APPIMAGE")
    assert install_kind() == "linux"


def test_the_server_asks_only_as_config_toml_says(tmp_path, monkeypatch):
    from libre_panel.config import set_config_value
    from libre_panel.editor.server import EditorState

    monkeypatch.setenv("LIBRE_PANEL_HOME", str(tmp_path))
    config = tmp_path / "config.toml"
    state = EditorState(config)
    asked = []
    state.updates.fetch = lambda: asked.append(1) or answer()
    from libre_panel.config import load_config

    assert load_config(config).check_updates is None
    assert state.updates.state(load_config(config).check_updates)["check"] is None
    set_config_value("check_updates", True, path=config)
    assert load_config(config).check_updates is True
    assert state.updates.state(load_config(config).check_updates)["newer"] and asked == [1]


# -- "Update to 9.1.0": the release's file for this install, fetched, checked, put in place --------

FILES = {
    "setup": "libre-panel-9.1.0-windows-x64-setup.exe",
    "portable": "libre-panel-9.1.0-windows-x64-portable.zip",
    "appimage": "Libre_Panel-9.1.0-x86_64.AppImage",
    "linux": "libre-panel-9.1.0-linux-x64.tar.gz",
}


def release(tag="v9.1.0", sums=True, **flags):
    """A release as GitHub's API gives it, with every file release.yml attaches."""
    names = [name.replace("9.1.0", tag[1:]) for name in FILES.values()]
    names += [f"libre_panel-{tag[1:]}-py3-none-any.whl"] + (["SHA256SUMS"] if sums else [])
    assets = [
        {"name": name, "size": 1000 + n, "browser_download_url": f"{RELEASE_FILES}{tag}/{name}"}
        for n, name in enumerate(names)
    ]
    return {**answer(tag), "assets": assets, **flags}


def test_the_release_s_file_for_each_kind_of_install(rig):
    for kind, name in FILES.items():
        latest = updates._release(release(), kind)
        assert latest["asset"] == {
            "name": name,
            "url": f"{RELEASE_FILES}v9.1.0/{name}",
            "size": 1000 + list(FILES).index(kind),
        }
        assert latest["sums"] == f"{RELEASE_FILES}v9.1.0/SHA256SUMS"
    assert updates._release(release(), "python")["asset"] is None  # pip: the command
    # a file of another release, of another project or elsewhere is not taken; nor odd names
    other = release()
    other["assets"][0]["browser_download_url"] = f"{RELEASE_FILES}v9.0.0/{FILES['setup']}"
    other["assets"][2]["browser_download_url"] = (
        "https://github.com/evil/x/releases/download/v9.1.0/a"
    )
    other["assets"][3]["name"] = "../libre-panel-9.1.0-linux-x64.tar.gz"
    for kind in ("setup", "appimage", "linux"):
        assert updates._release(other, kind)["asset"] is None
    assert updates._release(release(sums=False), "setup")["sums"] == ""


def test_only_releases_are_offered_and_a_pre_release_hears_of_its_own_minor_too():
    pre = {"prerelease": True}
    listed = [
        release("v0.9.0", draft=True),  # a draft is never offered
        release("v0.8.0rc1", **pre),  # another minor version's pre-release
        release("v0.7.1rc5", **pre),
        release("v0.7.0"),
    ]
    version = lambda data, current: (updates._release(data, "setup", current) or {}).get("version")  # noqa: E731
    assert version(listed, "0.7.1rc4") == "0.7.1rc5"
    assert version([release("v0.7.1"), *listed], "0.7.1rc4") == "0.7.1"
    assert version(listed, "0.7.0") == "0.7.0"  # a release that runs: releases only
    assert version(release("v0.7.1rc5", **pre), "0.7.0") is None
    assert version(release("v0.7.2rc1"), "0.7.1") is None  # (marked as one by its version)
    assert version(release("v9.1.0", draft=True), "0.7.0") is None
    assert version([], "0.7.1rc4") is None


def test_a_pre_release_asks_github_for_the_list_a_release_for_the_latest(monkeypatch):
    asked = []

    def urlopen(request, timeout):
        asked.append(request.full_url)
        return io.BytesIO(b"{}")

    monkeypatch.setattr(updates.urllib.request, "urlopen", urlopen)
    for running in ("0.7.1rc4", "0.7.1"):
        monkeypatch.setattr(updates, "__version__", running)
        updates.fetch_latest()
    assert asked == [updates.RELEASES_URL, updates.LATEST_URL]


class Response(io.BytesIO):
    """An answer of GitHub to a download, read as urllib's is; ``each`` hears of every read."""

    def __init__(self, data, length=None, each=None):
        super().__init__(data)
        self.headers = {"Content-Length": str(len(data) if length is None else length)}
        self.each = each

    def read(self, size=-1):
        if self.each:
            self.each()
        return super().read(min(size, 4096) if size and size > 0 else size)


class Files:
    """The files of the release on GitHub, as ``open_url`` of an Install fetches them."""

    def __init__(self, data, sums=None, each=None):
        self.data, self.each, self.asked = data, each, []
        self.sums = (
            sums
            if sums is not None
            else "".join(
                f"{hashlib.sha256(content).hexdigest()}  {name}\n" for name, content in data.items()
            )
        )

    def __call__(self, url):
        self.asked.append(url)
        name = url.rsplit("/", 1)[1]
        if name == "SHA256SUMS":
            return Response(self.sums.encode())
        return Response(self.data[name], each=self.each)


def latest(kind, size, sums=True):
    return updates._release(
        {**release(sums=sums), "assets": [
            {**a, "size": size} if a["name"] == FILES.get(kind) else a
            for a in release(sums=sums)["assets"]
        ]},
        kind,
    )  # fmt: skip


def rig_install(tmp_path, kind, content, sums=None, each=None):
    files = Files({FILES.get(kind, "pip"): content}, sums, each)
    spawned, shown = [], []
    install = Install(
        kind=lambda: kind,
        open_url=files,
        spawn=lambda command, cwd: spawned.append((command, cwd)),
        show=shown.append,
        temp=tmp_path,
    )
    install.files, install.spawned, install.shown = files, spawned, shown
    return install


def done(install):
    install.thread.join(10)
    return install.state()


def test_the_download_says_how_far_it_is_and_is_checked_against_the_release_s_sums(tmp_path):
    content = bytes(range(256)) * 400  # 100 KB, read 4 KB at a time
    seen = []
    install = rig_install(tmp_path, "setup", content, each=lambda: seen.append(install.state()))
    assert install.state()["state"] == "idle"
    first = install.download(latest("setup", len(content)))
    assert first["state"] == "downloading" and first["version"] == "9.1.0"
    assert first["how"] == "setup" and first["file"] == FILES["setup"]
    state = done(install)
    assert state["state"] == "ready" and state["checked"] and state["error"] == ""
    assert state["done"] == state["total"] == len(content)
    on_the_way = [s["done"] for s in seen if s["state"] == "downloading"]
    assert on_the_way == sorted(on_the_way) and 0 < on_the_way[10] < len(content)
    assert {s["total"] for s in seen} == {len(content)}
    file = Path(state["where"]) / FILES["setup"]
    assert file.read_bytes() == content and file.parent.parent == tmp_path
    assert install.files.asked == [
        f"{RELEASE_FILES}v9.1.0/{FILES['setup']}",
        f"{RELEASE_FILES}v9.1.0/SHA256SUMS",
    ]


def test_a_download_that_is_not_the_released_file_is_not_kept(tmp_path):
    content = b"setup" * 1000
    wrong = f"{'0' * 64}  {FILES['setup']}\n"
    install = rig_install(tmp_path, "setup", content, sums=wrong)
    install.download(latest("setup", len(content)))
    state = done(install)
    assert state["state"] == "error" and "SHA-256 differs" in state["error"]
    assert list(Path(state["where"]).iterdir()) == []  # nothing left of it
    with pytest.raises(UpdateError):
        install.install(lambda: None)  # nothing to install
    # the sums name another file, the download breaks off, the network is down
    install = rig_install(tmp_path, "setup", content, sums=f"{'0' * 64}  other.exe\n")
    install.download(latest("setup", len(content)))
    assert "do not name" in done(install)["error"]
    install = rig_install(tmp_path, "setup", content)
    install.download(latest("setup", len(content) + 10))
    assert "broke off" in done(install)["error"]
    install = rig_install(tmp_path, "setup", content)
    install.download(latest("setup", len(content) - 10))
    assert "longer than GitHub says" in done(install)["error"]
    install = rig_install(tmp_path, "setup", content)
    install.open_url = lambda url: (_ for _ in ()).throw(urllib.error.URLError("no network"))
    install.download(latest("setup", len(content)))
    assert done(install)["error"] == "The download did not work: no network"
    # and again: this time it comes
    install.open_url = install.files
    install.download(latest("setup", len(content)))
    assert done(install)["state"] == "ready"


def test_a_release_without_sums_has_its_size_checked(tmp_path):
    content = b"zip" * 100
    install = rig_install(tmp_path, "setup", content)
    install.download(latest("setup", len(content), sums=False))
    state = done(install)
    assert state["state"] == "ready" and not state["checked"]
    assert install.files.asked == [f"{RELEASE_FILES}v9.1.0/{FILES['setup']}"]


def test_only_a_newer_release_s_own_file_is_fetched_once_at_a_time(tmp_path):
    install = rig_install(tmp_path, "setup", b"x")
    with pytest.raises(UpdateError, match="no newer version"):
        install.download(None)
    with pytest.raises(UpdateError, match="no newer version"):
        install.download({**latest("setup", 1), "version": "0.0.1"})
    elsewhere = latest("setup", 1)
    elsewhere["asset"] = {**elsewhere["asset"], "url": "https://evil.example/setup.exe"}
    with pytest.raises(UpdateError, match="not one of Libre Panel's releases"):
        install.download(elsewhere)
    with pytest.raises(UpdateError, match="for this kind of install"):
        rig_install(tmp_path, "python", b"x").download(latest("python", 1))
    assert install.files.asked == [] and install.state()["state"] == "idle"
    # a second click while it downloads is told the first one's state
    go = threading.Event()
    install = rig_install(tmp_path, "setup", b"x" * 10_000, each=lambda: go.wait(10))
    install.download(latest("setup", 10_000))
    again = install.download(latest("setup", 10_000))
    assert again["state"] == "downloading"
    go.set()
    assert (
        done(install)["state"] == "ready"
        and install.files.asked.count(f"{RELEASE_FILES}v9.1.0/{FILES['setup']}") == 1
    )


def script(command):
    """The PowerShell script of a command made by restart_command."""
    assert command[0].endswith(r"\System32\WindowsPowerShell\v1.0\powershell.exe")
    assert command[-2] == "-EncodedCommand" and "-NoProfile" in command
    return base64.b64decode(command[-1]).decode("utf-16-le").splitlines()


def test_the_commands_that_start_the_new_version_once_this_one_has_quit():
    program = PureWindowsPath(r"C:\Program Files\Libre Panel\LibrePanel.exe")
    setup = PureWindowsPath(r"C:\Users\Kim\AppData\Local\Temp\lp-1\libre-panel-9.1.0-setup.exe")
    assert script(restart_command("setup", 4321, program, setup, "ab12" * 16)) == [
        "$ErrorActionPreference = 'SilentlyContinue'",
        "Wait-Process -Id 4321 -Timeout 60",
        # only the file that was checked, as test_installer.py installs it
        f"if ((Get-FileHash -Algorithm SHA256 -LiteralPath '{setup}').Hash -eq '{'ab12' * 16}') "
        f"{{ Start-Process -FilePath '{setup}' -ArgumentList '/VERYSILENT','/SUPPRESSMSGBOXES',"
        "'/NORESTART' -Wait }",
        f"Start-Process -FilePath '{program}' -WorkingDirectory '{program.parent}'",
        f"Remove-Item -LiteralPath '{setup.parent}' -Recurse -Force",
    ]
    # the portable copy's new folder; a quote in a path stays in it (also a typographic one)
    portable = PureWindowsPath("D:\\Kim\u2019s 'tools'\\libre-panel-9.1.0\\LibrePanel.exe")
    lines = script(restart_command("portable", 4321, portable))
    assert lines[1:] == [
        "Wait-Process -Id 4321 -Timeout 60",
        "Start-Process -FilePath 'D:\\Kim\u2019\u2019s ''tools''\\libre-panel-9.1.0\\"
        "LibrePanel.exe' -WorkingDirectory 'D:\\Kim\u2019\u2019s ''tools''\\libre-panel-9.1.0'",
    ]
    image = Path("/home/kim/Apps/Libre_Panel-0.7.0-x86_64.AppImage")
    assert restart_command("appimage", 4321, image) == [
        "/bin/sh", "-c", WAIT_THEN_START, "libre-panel-update", "4321", str(image)
    ]  # fmt: skip


def test_install_and_restart_runs_the_setup_once_the_app_has_quit(tmp_path, monkeypatch):
    monkeypatch.setattr(updates.sys, "executable", str(tmp_path / "app" / "libre-panel.exe"))
    install = rig_install(tmp_path, "setup", b"setup")
    with pytest.raises(UpdateError, match="Nothing is downloaded"):
        install.install(lambda: None)
    install.download(latest("setup", 5))
    file = Path(done(install)["where"]) / FILES["setup"]
    with pytest.raises(UpdateError, match="Only the running app"):
        install.install(None)  # an editor without the app: it would not quit
    quit = []
    assert install.install(lambda: quit.append(1))["state"] == "installing"
    state = done(install)
    assert state["state"] == "installing" and quit == [1]
    [(command, cwd)] = install.spawned
    lines = script(command)
    assert lines[1] == f"Wait-Process -Id {updates.os.getpid()} -Timeout 60"
    checked = hashlib.sha256(b"setup").hexdigest()
    assert lines[2].startswith(f"if ((Get-FileHash -Algorithm SHA256 -LiteralPath '{file}').Hash")
    assert f"-eq '{checked}') {{ Start-Process -FilePath '{file}' -ArgumentList" in lines[2]
    assert lines[3].startswith(f"Start-Process -FilePath '{tmp_path / 'app' / 'LibrePanel.exe'}'")
    assert cwd == tmp_path  # not in the folder the script removes


def zipped(files):
    data = io.BytesIO()
    with zipfile.ZipFile(data, "w") as archive:
        for name, content in files.items():
            archive.writestr(name, content)
    return data.getvalue()


def test_the_portable_zip_goes_into_a_new_folder_with_the_settings(tmp_path, monkeypatch):
    running = tmp_path / "sticks" / "libre-panel"
    (running / "data" / "themes").mkdir(parents=True)
    for name, content in {"LibrePanel.exe": "old", "portable.txt": "", "data/config.toml": "x=1",
                          "data/themes/mine.json": "{}", "data/libre-panel.lock": "1",
                          "data/instance.json": "{}"}.items():  # fmt: skip
        (running / name).write_text(content)
    (tmp_path / "sticks" / "libre-panel-9.1.0").mkdir()  # taken: the next name
    monkeypatch.setattr(updates.sys, "executable", str(running / "LibrePanel.exe"))
    content = zipped({"LibrePanel.exe": "new", "portable.txt": "", "_internal/a.dll": "a"})
    install = rig_install(tmp_path / "temp", "portable", content)
    (tmp_path / "temp").mkdir()
    install.download(latest("portable", len(content)))
    download = Path(done(install)["where"])
    quit = []
    install.install(lambda: quit.append(1))
    state = done(install)
    new = tmp_path / "sticks" / "libre-panel-9.1.0-2"
    assert state["where"] == str(new) and quit == [1]
    assert (new / "LibrePanel.exe").read_text() == "new" and (new / "_internal" / "a.dll").exists()
    assert (new / "data" / "config.toml").read_text() == "x=1"
    assert (new / "data" / "themes" / "mine.json").exists()
    assert not (new / "data" / "libre-panel.lock").exists()  # the running app's own
    assert not (new / "data" / "instance.json").exists()
    assert (running / "LibrePanel.exe").read_text() == "old"  # the running copy stays
    assert not download.exists()  # the zip is gone
    [(command, cwd)] = install.spawned
    assert cwd == new and script(command)[-1].startswith(f"Start-Process -FilePath '{new}")


def test_a_zip_that_is_no_portable_libre_panel_is_not_put_in_place(tmp_path, monkeypatch):
    running = tmp_path / "libre-panel"
    running.mkdir()
    monkeypatch.setattr(updates.sys, "executable", str(running / "LibrePanel.exe"))
    for content in (zipped({"../evil.exe": "x", "LibrePanel.exe": "", "portable.txt": ""}),
                    zipped({"readme.txt": "x"}), b"no zip at all"):  # fmt: skip
        install = rig_install(tmp_path, "portable", content)
        install.download(latest("portable", len(content)))
        done(install)
        quit = []
        install.install(lambda quit=quit: quit.append(1))
        state = done(install)
        assert state["state"] == "error" and state["error"], content
        assert quit == [] and install.spawned == []  # the app runs on
    assert sorted(path.name for path in tmp_path.iterdir() if "9.1.0" in path.name) == []
    assert not (tmp_path / "evil.exe").exists()


def test_a_portable_copy_s_autostart_names_the_new_folder():
    from test_service import FakeRegistry

    from libre_panel.autostart import APP_NAME, Autostart

    registry = FakeRegistry()
    autostart = Autostart(platform="win32", registry=registry, admin=lambda: False)
    old, new = PureWindowsPath(r"D:\libre-panel"), PureWindowsPath(r"D:\libre-panel-9.1.0")
    registry.set(APP_NAME, r"D:\libre-panel\LibrePanel.exe tray --background")
    move_autostart(old, new, autostart)
    assert registry.get(APP_NAME) == r"D:\libre-panel-9.1.0\LibrePanel.exe tray --background"
    # another copy's autostart (an installed one) stays as it is
    registry.set(APP_NAME, r'"C:\Program Files\Libre Panel\LibrePanel.exe" tray --background')
    move_autostart(old, new, autostart)
    assert registry.get(APP_NAME).startswith('"C:\\Program Files')


def test_the_appimage_takes_the_running_one_s_place(tmp_path, monkeypatch):
    image = tmp_path / "Apps" / "Libre_Panel-0.7.0-x86_64.AppImage"
    image.parent.mkdir()
    image.write_bytes(b"old")
    monkeypatch.setenv("APPIMAGE", str(image))
    install = rig_install(tmp_path, "appimage", b"new AppImage")
    install.download(latest("appimage", 12))
    state = done(install)
    downloaded = image.with_name(FILES["appimage"])
    assert state["where"] == str(image.parent) and downloaded.read_bytes() == b"new AppImage"
    if os.name != "nt":  # (Windows has no execute bit to set)
        assert downloaded.stat().st_mode & 0o111
    assert image.read_bytes() == b"old"  # not yet
    quit = []
    install.install(lambda: quit.append(1))
    done(install)
    assert image.read_bytes() == b"new AppImage" and image.stat().st_mode & 0o111
    assert not downloaded.exists() and quit == [1]
    assert install.spawned == [
        (restart_command("appimage", updates.os.getpid(), image), image.parent)
    ]


def test_an_appimage_in_a_folder_it_may_not_write_is_not_fetched(tmp_path, monkeypatch):
    monkeypatch.setenv("APPIMAGE", str(tmp_path / "Apps" / "Libre_Panel.AppImage"))
    monkeypatch.setattr(updates.os, "access", lambda path, mode: False)
    install = rig_install(tmp_path, "appimage", b"x")
    with pytest.raises(UpdateError, match="cannot be written to"):
        install.download(latest("appimage", 1))
    assert install.files.asked == []


def test_the_tarball_goes_into_downloads_and_its_folder_is_shown(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("USERPROFILE", str(tmp_path))  # (Path.home() on Windows)
    (tmp_path / "Downloads").mkdir()
    install = rig_install(tmp_path, "linux", b"tarball")
    install.download(latest("linux", 7))
    state = done(install)
    assert state["how"] == "open" and state["where"] == str(tmp_path / "Downloads")
    assert (tmp_path / "Downloads" / FILES["linux"]).read_bytes() == b"tarball"
    assert install.install(None)["state"] == "ready"  # nothing quits for it
    assert install.shown == [tmp_path / "Downloads"] and install.spawned == []


def test_downloads_are_fetched_over_https_only():
    with pytest.raises(urllib.error.URLError):
        open_https("http://github.com/syntensa/libre-panel/releases/download/v9.1.0/x")
    handler = updates._HttpsOnly()
    request = urllib.request.Request(f"{RELEASE_FILES}v9.1.0/x")
    with pytest.raises(urllib.error.URLError, match="not HTTPS"):
        handler.redirect_request(request, None, 302, "Found", {}, "http://objects.example/x")
    sent_on = handler.redirect_request(request, None, 302, "Found", {}, "https://objects.example/x")
    assert sent_on.full_url == "https://objects.example/x"


def test_the_server_downloads_and_installs_for_the_running_app(tmp_path, monkeypatch):
    from test_editor_server import request

    from libre_panel.config import set_config_value
    from libre_panel.editor.server import make_server

    set_config_value("check_updates", True)
    monkeypatch.setattr(updates.sys, "executable", str(tmp_path / "LibrePanel.exe"))
    acted = []
    controls = type("Controls", (), {"act": lambda self, action: acted.append(action)})()
    srv = make_server(port=0, controls=controls)
    thread = threading.Thread(target=srv.serve_forever, daemon=True)
    thread.start()
    port = srv.server_address[1]
    try:
        state = srv.editor_state
        state.updates.fetch = lambda: release()
        state.updates.kind = lambda: "setup"
        state.install = rig_install(tmp_path, "setup", b"x" * 1000)
        assert request(port, "GET", "/api/update/install") == (200, {**updates.IDLE})
        status, answer = request(port, "POST", "/api/update/install", {"action": "install"})
        assert status == 409 and answer["error"] == "Nothing is downloaded to install yet."
        status, answer = request(port, "POST", "/api/update/install", {"action": "download"})
        assert status == 200 and answer["state"] == "downloading"
        state.install.thread.join(10)
        assert request(port, "GET", "/api/update/install")[1]["state"] == "ready"
        status, answer = request(port, "POST", "/api/update/install", {"action": "install"})
        assert status == 200 and answer["state"] == "installing"
        state.install.thread.join(10)
        assert acted == ["quit"] and len(state.install.spawned) == 1
        assert request(port, "POST", "/api/update/install", {"action": "?"})[0] == 400
        state.updates.fetch = lambda: release("v0.0.1")  # nothing newer
        state.updates.state(True, now=True)
        status, answer = request(port, "POST", "/api/update/install", {"action": "download"})
        assert status == 409 and answer["error"] == "There is no newer version to update to."
    finally:
        srv.shutdown()
        srv.server_close()
        srv.editor_state.close()


def test_the_release_names_its_files_as_update_to_looks_for_them():
    """release.yml (and the setup's script) name each download with the end Update to looks for,
    and the release carries SHA256SUMS."""
    root = Path(__file__).resolve().parent.parent
    workflow = (root / ".github" / "workflows" / "release.yml").read_text(encoding="utf-8")
    setup = (root / "packaging" / "windows" / "libre-panel.iss").read_text(encoding="utf-8-sig")
    assert "OutputBaseFilename=libre-panel-{#AppVersion}-windows-x64-setup\n" in setup
    for kind, ending in updates.ASSET_ENDINGS.items():
        assert ending in workflow or kind == "setup" and ending.removesuffix(".exe") in setup
    assert "sha256sum -- *) > SHA256SUMS" in workflow and updates.CHECKSUMS == "SHA256SUMS"
