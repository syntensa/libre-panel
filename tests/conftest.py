import itertools
import os
import sys
import textwrap

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
    sys.__stdout__.write(f"\n::error file={path},line={(line or 0) + 1},title={title}::{text}\n")
    sys.__stdout__.flush()


@pytest.fixture(autouse=True)
def isolated_home(tmp_path, monkeypatch):
    """Every test gets its own config/user-themes directory."""
    home = tmp_path / "home"
    monkeypatch.setenv("LIBRE_PANEL_HOME", str(home))
    return home


@pytest.fixture(autouse=True)
def no_installed_plugins(monkeypatch):
    """Plugins installed next to Libre Panel (a mod) stay out of the tests;
    tests install their own as folders."""
    from libre_panel.plugins import loader

    monkeypatch.setattr(loader, "_installed", lambda group: iter(()))
    loader.reset_registry()
    yield
    loader.reset_registry()


class NoTasks:
    """A Task Scheduler without tasks, for tests that do not bring their own."""

    def exists(self, name):
        return False

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
