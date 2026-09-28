import itertools
import sys
import textwrap

import pytest

from libre_panel import i18n


@pytest.fixture(autouse=True)
def isolated_home(tmp_path, monkeypatch):
    """Every test gets its own config/user-themes directory."""
    home = tmp_path / "home"
    monkeypatch.setenv("LIBRE_PANEL_HOME", str(home))
    return home


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

    def make(source, parts, api=1):
        """``parts``: {group: {name: "attribute"}}; attributes are in the new package."""
        package = f"lp_test_plugin_{next(_plugin_names)}"
        folder = isolated_home / "plugins" / package
        (folder / package).mkdir(parents=True)
        (folder / package / "__init__.py").write_text(textwrap.dedent(source), encoding="utf-8")
        tables = "".join(
            f'\n[entry-points."{group}"]\n'
            + "".join(f'"{name}" = "{package}:{attr}"\n' for name, attr in names.items())
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


def _reset_registry():
    from libre_panel.plugins.loader import reset_registry

    reset_registry()
