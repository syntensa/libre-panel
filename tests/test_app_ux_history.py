"""History in a real browser: what it says while nothing is kept and the switch that keeps it,
then the course of a week of readings, what it came to and the games; skipped without
Playwright or Chromium."""

import sqlite3
import time

import pytest

pytest.importorskip("playwright.sync_api")

from playwright.sync_api import expect  # noqa: E402
from test_app_browser import (  # noqa: E402, F401
    PICTURE,
    app_url,
    browser_page,
    choose_theme,
    settings_config,
    waits_for,
)
from test_history import minute, session  # noqa: E402

from libre_panel.services.memory_store import SCHEMA  # noqa: E402


def kept(home, days=3):
    """A memory with ``days`` of minutes up to now (an hour of a game each evening) and rounds."""
    folder = home / "plugins-data" / "memory"
    folder.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(folder / "memory.db")
    con.executescript(SCHEMA)
    now = int(time.time()) // 60 * 60
    rows = []
    for i in range(days * 1440):
        playing = 60 <= i % 1440 < 120  # an hour a day, an hour before now
        rows.append(
            minute(
                now - i * 60,
                cpu=72 if playing else 45,
                gpu=70 if playing else 40,
                game="Cyber Racer" if playing else None,
                peak=84 if playing else 50,
            )
        )
    con.executemany(f"INSERT INTO minutes VALUES ({','.join('?' * len(rows[0]))})", rows)
    for day in range(days):
        row = session(now - day * 86400 - 7200, "Cyber Racer", 3600, 140.0, 96.0, cpu_max=84.0)
        con.execute(
            f"INSERT INTO sessions ({', '.join(row)}) VALUES ({','.join('?' * len(row))})",
            list(row.values()),
        )
    con.commit()
    con.close()  # fmt: skip


def test_without_the_memory_history_says_so_and_switches_it_on(app_url, isolated_home):  # noqa: F811
    choose_theme(isolated_home, "lumen")
    with browser_page(app_url, "history", allow=PICTURE) as page:
        off = page.locator("[data-history='off']")
        expect(off).to_contain_text("No history is kept yet")
        expect(off).to_contain_text("on this computer only")
        off.get_by_role("button", name="Keep a history").click()
        waits_for(page, lambda: "memory" in settings_config().services.enabled)
        expect(page.locator("[data-history='empty']")).to_contain_text(
            "Nothing kept in this time yet"
        )


def test_a_day_and_a_week_of_readings_with_the_games(app_url, isolated_home):  # noqa: F811
    from libre_panel.config import set_config_value

    choose_theme(isolated_home, "lumen")
    set_config_value("services.enabled", ["memory"])
    kept(isolated_home)
    with browser_page(app_url, "history", allow=PICTURE) as page:
        tiles = page.locator(".history-tile")
        expect(tiles.nth(0)).to_contain_text("CPU temperature")
        expect(tiles.nth(0)).to_contain_text("peak 84 °C")
        expect(tiles.nth(2)).to_contain_text("24 h")  # a day kept, a minute at a time
        expect(tiles.nth(2)).to_contain_text("of which 1 h in games")
        for chart in ("temp", "load", "power"):
            line = page.locator(f"[data-chart='{chart}'] path.history-line").first
            assert len(line.get_attribute("d")) > 20, chart
        temp = page.locator("[data-chart='temp']")
        expect(temp.locator("svg")).to_have_attribute(
            "aria-label", "Temperature: CPU 45 to 72 °C, GPU 40 to 70 °C"
        )
        box = temp.locator("svg").bounding_box()
        page.mouse.move(box["x"] + box["width"] * 0.6, box["y"] + 40)
        expect(temp.locator(".history-tip")).to_contain_text("CPU")
        expect(page.locator(".history-games tbody tr")).to_have_count(1)
        expect(page.locator(".history-games tbody tr")).to_contain_text("Cyber Racer")
        expect(page.locator(".history-round-list li")).to_have_count(1)  # today's
        with page.expect_request(lambda r: "/api/history?span=week" in r.url):
            page.get_by_role("radio", name="7 days").click()
        assert page.url.endswith("#/history/week")
        expect(page.locator(".history-round-list li")).to_have_count(3)
        expect(tiles.nth(2)).to_contain_text("72 h")
