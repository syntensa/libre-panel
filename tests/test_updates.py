"""Whether a newer Libre Panel is out (editor/updates.py): asked only where it is switched on,
once a day, kept in updates.json, and what Start is told of it."""

import json

import pytest

from libre_panel import __version__
from libre_panel.editor import updates
from libre_panel.editor.updates import Updates, install_kind, newer, version_key

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
