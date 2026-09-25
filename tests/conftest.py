import pytest


@pytest.fixture(autouse=True)
def isolated_home(tmp_path, monkeypatch):
    """Every test gets its own config/user-themes directory."""
    home = tmp_path / "home"
    monkeypatch.setenv("LIBRE_PANEL_HOME", str(home))
    return home
