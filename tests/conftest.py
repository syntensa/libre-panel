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
