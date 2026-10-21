"""French, Spanish, Polish, Portuguese (Brazil) and Simplified Chinese: each catalog complete and
in step with the German one, which test_i18n holds to the code."""

import json
import re
from datetime import datetime

import pytest
from i18n_keys import all_messages, tables

from libre_panel import i18n
from libre_panel.i18n import format_date

LANGUAGES = ["fr", "es", "pl", "pt", "zh"]
PLACEHOLDER = re.compile(r"\{[^{}]*\}")
SHY = "­"


def catalog(language):
    return json.loads((i18n.LOCALE_DIR / f"{language}.json").read_text(encoding="utf-8"))


@pytest.fixture(autouse=True)
def _english_after():
    yield
    i18n.set_language("en")


def test_every_language_has_a_name_and_a_catalog():
    assert set(i18n.LANGUAGES) == {"en", "de", *LANGUAGES}
    files = sorted(path.stem for path in i18n.LOCALE_DIR.glob("*.json"))
    assert files == sorted(["de", *LANGUAGES])


@pytest.mark.parametrize("language", LANGUAGES)
def test_every_text_is_translated_and_none_is_stale(language):
    messages = catalog(language)["messages"]
    missing = sorted(all_messages() - set(messages))
    stale = sorted(set(messages) - all_messages())
    assert missing == [], f"add these to locale/{language}.json:\n" + "\n".join(missing)
    assert stale == [], f"no longer used, remove from locale/{language}.json:\n" + "\n".join(stale)


@pytest.mark.parametrize("language", LANGUAGES)
def test_no_translation_is_empty_and_the_placeholders_match(language):
    data = catalog(language)
    for table in (table for table in data.values() if isinstance(table, dict)):
        assert all(text.strip() for text in table.values()), language
    for english, text in data["messages"].items():
        assert sorted(PLACEHOLDER.findall(english)) == sorted(PLACEHOLDER.findall(text)), english
    assert SHY not in "".join(data["messages"].values())  # (the panel draws those: no box)


@pytest.mark.parametrize("language", LANGUAGES)
def test_each_text_once(language):
    twice = []

    def one_of_each(pairs):
        names = [name for name, _ in pairs]
        twice.extend(sorted({name for name in names if names.count(name) > 1}))
        return dict(pairs)

    text = (i18n.LOCALE_DIR / f"{language}.json").read_text(encoding="utf-8")
    json.loads(text, object_pairs_hook=one_of_each)
    assert twice == [], f"in locale/{language}.json more than once, keep one:\n" + "\n".join(twice)


@pytest.mark.parametrize("language", LANGUAGES)
@pytest.mark.parametrize("table", ["fields", "widgets", "enums", "icons"])
def test_editor_tables_are_complete(language, table):
    assert set(catalog(language)[table]) == tables()[table]


@pytest.mark.parametrize("language", LANGUAGES)
def test_widget_names_fit_a_tile_of_the_library(language):
    """Three columns of 75 px: a piece of more than 11 letters breaks mid-syllable."""
    for key, name in catalog(language)["widgets"].items():
        for word in re.split(r"[\s\-]+", name):
            pieces = word.split(SHY)
            assert len(pieces) == 1 or min(map(len, pieces)) >= 3, (key, "a soft hyphen at an end")
            assert max(map(len, pieces)) <= 11, f"{key}: {name!r}"


@pytest.mark.parametrize("language", LANGUAGES)
def test_names_of_days_and_months(language):
    data = catalog(language)
    assert len(data["days"]) == len(data["days_short"]) == 7
    assert len(data["months"]) == len(data["months_short"]) == 12
    assert len(set(data["days"])) == 7 and len(set(data["months"])) == 12


@pytest.mark.parametrize(
    ("language", "expected"),
    [
        ("fr", "dimanche 27 septembre"),
        ("es", "domingo 27 de septiembre"),
        ("pl", "niedziela 27 września"),
        ("pt", "domingo 27 de setembro"),
        ("zh", "星期日 9月27日"),
    ],
)
def test_dates(language, expected):
    i18n.set_language(language)
    assert format_date(datetime(2026, 9, 27), "%A %d %B") == expected
    assert format_date(datetime(2026, 9, 27), "%B") == catalog(language)["months"][8]


class Strict(datetime):
    """A moment whose strftime takes only ASCII, as Windows' Python before 3.12 does: its
    format goes through the ANSI code page, where "września" comes out wrong."""

    def strftime(self, fmt):
        assert fmt.isascii(), fmt
        return super().strftime(fmt)


@pytest.mark.parametrize("language", ["en", "de", *LANGUAGES])
def test_no_name_goes_through_strftime(language):
    i18n.set_language(language)
    moment = Strict(2026, 9, 27, 21, 5)
    text = format_date(moment, "%A, %d %B %Y · %H:%M · %a %b · 100%% · Ühr")
    names = catalog(language) if language != "en" else {}
    assert text.endswith("· 100% · Ühr") and "21:05" in text and "2026" in text
    if names:
        assert names["days"][6] in text and names["days_short"][6] in text
    assert format_date(moment, "%d %B") == format_date(datetime(2026, 9, 27), "%d %B")


@pytest.mark.parametrize("language", LANGUAGES)
def test_texts_switch_with_the_language(language):
    i18n.set_language(language)
    assert i18n.t("Save") == catalog(language)["messages"]["Save"] != "Save"
    assert i18n.t("{n} min", n=5) == "5 min"
