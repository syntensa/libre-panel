"""Whom Start tells what is new in a release (editor/news.py, /api/news)."""

import json

import pytest
from test_editor_server import request, server  # noqa: F401

from libre_panel import __version__
from libre_panel.editor.news import News


def choose_theme(home, theme="lumen"):
    home.mkdir(parents=True, exist_ok=True)
    (home / "config.toml").write_text(f'theme = "{theme}"\n', encoding="utf-8")


def test_a_first_start_of_this_version_is_kept_as_such(isolated_home):
    """Who began with this version gets the first steps, not the news: that is worked out as the
    app starts, before the first start chooses a theme, and kept from the first ask on."""
    news = News(isolated_home / "config.toml")
    assert news.state() == {"first": __version__, "seen": ""}
    assert not (isolated_home / "news.json").exists()  # no folder yet: nothing is made for it
    choose_theme(isolated_home)  # the first start's choice
    assert news.state() == {"first": __version__, "seen": ""}
    saved = json.loads((isolated_home / "news.json").read_text(encoding="utf-8"))
    assert saved == {"first": __version__, "seen": ""}
    # the next start finds a theme in config.toml, and the file says it was chosen in this one
    assert News(isolated_home / "config.toml").state()["first"] == __version__


def test_who_updated_is_told_once(isolated_home):
    """A config.toml that named a theme before news.json was there is an older version's."""
    choose_theme(isolated_home)
    news = News(isolated_home / "config.toml")
    assert news.state() == {"first": "older", "seen": ""}
    assert news.see("0.6") == {"first": "older", "seen": "0.6"}
    assert News(isolated_home / "config.toml").state() == {"first": "older", "seen": "0.6"}
    with pytest.raises(ValueError):
        news.see("all of them")


def test_a_file_that_cannot_be_read_is_made_anew(isolated_home):
    choose_theme(isolated_home)
    (isolated_home / "news.json").write_text("{not json", encoding="utf-8")
    assert News(isolated_home / "config.toml").state() == {"first": "older", "seen": ""}
    (isolated_home / "news.json").write_text('{"first": "0.6.0", "seen": 6}', encoding="utf-8")
    assert News(isolated_home / "config.toml").state() == {"first": "0.6.0", "seen": ""}


def test_the_news_over_the_api(server, isolated_home):  # noqa: F811
    status, news = request(server, "GET", "/api/news")
    assert status == 200 and news == {"first": __version__, "seen": ""}
    status, news = request(server, "POST", "/api/news", {"seen": "0.6"})
    assert status == 200 and news["seen"] == "0.6"
    for wrong in ({"seen": "soon"}, {"seen": 6}, ["0.6"]):
        status, answer = request(server, "POST", "/api/news", wrong)
        assert status == 400 and "release" in answer["error"], wrong
    assert request(server, "POST", "/api/news", b"{")[0] == 400
