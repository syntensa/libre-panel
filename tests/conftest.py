import itertools
import os
import sys
import textwrap
from pathlib import Path

import pytest

from libre_panel import i18n


def pytest_runtest_logreport(report):
    """On GitHub Actions a failed test becomes an annotation of the run, which the
    API shows without the log (whose download may not be reachable)."""
    if not report.failed or os.environ.get("GITHUB_ACTIONS") != "true":
        return
    path, line, _name = report.location
    text = (report.longreprtext or "").strip()[-1500:]
    for char, code in (("%", "%25"), ("\r", "%0D"), ("\n", "%0A")):
        text = text.replace(char, code)
    title = f"{report.nodeid} ({report.when})".replace(",", ";").replace("::", " ")
    # on a line of its own: pytest's progress dots have no line break
    out = f"\n::error file={path},line={(line or 0) + 1},title={title}::{text}\n"
    # what the console cannot write (a "ś" on Windows' cp1252) as "?", not a crash of the run
    encoding = sys.__stdout__.encoding or "utf-8"
    sys.__stdout__.write(out.encode(encoding, "replace").decode(encoding))
    sys.__stdout__.flush()


@pytest.fixture(autouse=True)
def isolated_home(tmp_path, monkeypatch):
    """Every test gets its own config/user-themes directory."""
    home = tmp_path / "home"
    monkeypatch.setenv("LIBRE_PANEL_HOME", str(home))
    return home


@pytest.fixture(autouse=True)
def editor_tokens(monkeypatch):
    """The token of every editor server a test starts is kept by its port (editor_token.py),
    for the clients of the tests to send: a server answers nothing without it. And the requests
    that server is still answering when the test ends are answered before the next one starts."""
    import threading

    import editor_token

    from libre_panel.editor.server import EditorHandler, EditorServer

    made, served = EditorServer.__init__, EditorHandler._serve
    under_way, count = threading.Condition(), [0]

    def init(self, *args, **kwargs):
        made(self, *args, **kwargs)
        editor_token.TOKENS[self.server_address[1]] = self.token

    def serve(self, method):
        with under_way:
            count[0] += 1
        try:
            return served(self, method)
        finally:
            with under_way:
                count[0] -= 1
                under_way.notify_all()

    monkeypatch.setattr(EditorServer, "__init__", init)
    monkeypatch.setattr(EditorHandler, "_serve", serve)
    yield
    # A request that a closed browser left under way (the library's pictures are drawn one after
    # another, a request each) goes on in its thread of the server, which shutting the server
    # down does not wait for. It ends before the next test starts: it would draw with the
    # renderer that test has put in place, and show up in what that test counts.
    with under_way:
        under_way.wait_for(lambda: count[0] == 0, timeout=120)
    editor_token.TOKENS.clear()


@pytest.fixture(autouse=True)
def not_elevated(monkeypatch):
    """The tests run with the rights of an ordinary user, whoever runs them (a CI runner on
    Windows is an administrator, a container is root): the settings that name a program change
    unless a test says Libre Panel runs with the highest rights."""
    from libre_panel.editor import access

    monkeypatch.setattr(access, "elevated", lambda: False)


@pytest.fixture(autouse=True)
def no_installed_plugins(monkeypatch):
    """Plugins installed next to Libre Panel (a mod) stay out of the tests;
    tests install their own as folders."""
    from libre_panel.plugins import loader

    monkeypatch.setattr(loader, "_installed", lambda group: iter(()))
    loader.reset_registry()
    yield
    loader.reset_registry()


@pytest.fixture(autouse=True)
def no_machine_names(monkeypatch):
    """What the computer running the tests is made of stays out of them (it differs from
    machine to machine, and the names are its owner's): nothing is found, until a test
    brings a computer of its own."""
    from libre_panel.sensors import hardware

    monkeypatch.setattr(hardware, "_found", None)  # what was asked before is not an answer
    monkeypatch.setattr(hardware, "_scan_machine", lambda *args: hardware.Found())


@pytest.fixture(autouse=True)
def no_community_list(monkeypatch):
    """The community's list of themes is never asked of GitHub: its address is one of this
    computer where nothing answers, until a test serves a list of its own."""
    from libre_panel.editor import community

    monkeypatch.setattr(community, "INDEX_URL", "http://127.0.0.1:9/community/themes/index.json")


@pytest.fixture(autouse=True)
def no_usb_panel(monkeypatch):
    """A panel plugged into the machine running the tests stays out of them: a config that
    names no theme looks for one on the USB bus to pick the panel's own. Nothing is on the
    bus, until a test says so."""
    from libre_panel.devices import turzx_usb

    monkeypatch.setattr(turzx_usb.UsbTransport, "present", staticmethod(lambda pid: False))


@pytest.fixture(autouse=True)
def no_linux_setup(monkeypatch):
    """The one-click setup of Linux never touches the machine running the tests: it is not
    asked about the board or the rights, and no test asks for an administrator's password.
    A test brings a computer (``linux_setup.Facts``) and a stand-in for the rights."""
    from libre_panel import linux_rights, linux_setup

    def forbidden(argv):
        raise AssertionError("a test asked for the administrator's rights")

    class Untouchable:
        """The computer a test runs on, which a test may not change (not even one that runs as
        root, as CI containers do): the first thing a setup does to it fails the test."""

        def __getattr__(self, name):
            raise AssertionError(f"a test changed the machine it runs on ({name})")

    monkeypatch.setattr(linux_setup, "gather", lambda *args, **kwargs: linux_setup.Facts())
    monkeypatch.setattr(linux_setup, "RealSystem", Untouchable)
    monkeypatch.setattr(linux_rights, "JOB", linux_rights.Job(runner=forbidden))
    monkeypatch.setattr(linux_rights, "imports_isolated", lambda python: False)


@pytest.fixture(autouse=True)
def no_machine_sensors(monkeypatch):
    """The Linux sensor source reads the folder a test lays out, not this machine's /sys."""
    from libre_panel.sensors import linux

    monkeypatch.setattr(linux, "ROOT", Path("/no/such/root"))


class NoTasks:
    """A Task Scheduler without tasks, for tests that do not bring their own."""

    def exists(self, name):
        return False

    def query(self, name):
        return None

    def create(self, name, xml):
        raise AssertionError("a test created a real scheduled task")

    def delete(self, name):
        pass


@pytest.fixture(autouse=True)
def no_scheduled_tasks(monkeypatch):
    """The machine's own Task Scheduler stays out of the tests (it may hold the
    elevated "Libre Panel" task)."""
    from libre_panel import autostart

    monkeypatch.setattr(autostart, "WindowsTasks", NoTasks)


@pytest.fixture(autouse=True)
def no_sensor_service(monkeypatch):
    """The sensor service of the machine running the tests (a Windows PC with Libre Panel
    installed) stays out of them: it is not installed, until a test says so."""
    from libre_panel.sensors import lhm_service

    monkeypatch.setattr(lhm_service, "registration", lambda name=None: None)


@pytest.fixture(autouse=True)
def english(monkeypatch):
    """Tests read English texts, whatever language the machine running them has."""
    monkeypatch.setattr(i18n, "system_language", lambda: "en")
    i18n.set_language("en")
    yield
    i18n.set_language("en")


_plugin_names = itertools.count()


@pytest.fixture
def plugin_folder(isolated_home):
    """A plugin installed as a folder, as in the Windows setup."""
    created = []

    def make(source, parts, api=1, files=None):
        """``parts``: {group: {name: "attribute"}}; attributes are in the new package
        (a target with ``{package}`` in it is taken as it is, e.g. ``"{package}.themes"``).
        ``files``: more files in the package folder, {relative path: text}."""
        package = f"lp_test_plugin_{next(_plugin_names)}"
        folder = isolated_home / "plugins" / package
        (folder / package).mkdir(parents=True)
        (folder / package / "__init__.py").write_text(textwrap.dedent(source), encoding="utf-8")
        for name, text in (files or {}).items():
            (folder / package / name).parent.mkdir(parents=True, exist_ok=True)
            (folder / package / name).write_text(text, encoding="utf-8")
        tables = "".join(
            f'\n[entry-points."{group}"]\n'
            + "".join(f'"{name}" = "{_target(package, attr)}"\n' for name, attr in names.items())
            for group, names in parts.items()
        )
        (folder / "plugin.toml").write_text(
            f'name = "{package}"\napi = {api}\n{tables}', encoding="utf-8"
        )
        created.append((package, folder))
        _reset_registry()
        return package

    yield make
    for package, folder in created:
        sys.modules.pop(package, None)
        if str(folder) in sys.path:
            sys.path.remove(str(folder))
    _reset_registry()


def _target(package, attr):
    return attr.format(package=package) if "{package}" in attr else f"{package}:{attr}"


def _reset_registry():
    from libre_panel.plugins.loader import reset_registry

    reset_registry()
