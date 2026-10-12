import pytest

from libre_panel.render.formatting import (
    FormatError,
    counts,
    human_bytes,
    human_duration,
    safe_format,
)


def test_basic_format():
    assert safe_format("{value:.0f}{unit}", 45.6, "°C") == "46°C"
    assert safe_format("{label}: {value}", 3, label="Fans") == "Fans: 3"


def test_extra_specs():
    assert safe_format("{value:bytes}/s", 2_400_000) == "2.4 MB/s"
    assert safe_format("{value:duration}", 3 * 86400 + 5 * 3600) == "3d 5h"
    assert human_bytes(512) == "512 B"
    assert human_duration(59) == "0m"


def test_a_number_counts_to_a_new_reading_a_text_or_a_songs_time_does_not():
    for fmt in ("{value:.0f}°", "{value:bytes}/s", "{value:gb} GB", "{value:rate_unit}",
                "{value:>5.1f}", "{value:d} RPM", "{value:.0%}", "{value:rate}"):  # fmt: skip
        assert counts(fmt), fmt
    for fmt in ("{value}", "{label}", "{value:clock}", "{value:duration}", "{value:upper}"):
        assert not counts(fmt), fmt


def test_rates_and_sizes_as_spur_ii_sets_them():
    # a rate: whole KB/s below 1 MB/s (idle traffic is not 0.0), MB/s with a decimal above
    assert safe_format("{value:rate} {value:rate_unit}", 43 * 1024) == "43 KB/s"
    assert safe_format("{value:rate} {value:rate_unit}", 1023 * 1024) == "1023 KB/s"
    assert safe_format("{value:rate} {value:rate_unit}", 3.5 * 1024**2) == "3.5 MB/s"
    # a size in GB, from the unit the reading comes in
    assert safe_format("{value:gb}", 13824, "MB") == "13.5"
    assert safe_format("{value:gb}", 17.0, "GiB") == "17.0"
    assert safe_format("{value:gb}", 5 * 1024**3, "B") == "5.0"
    assert safe_format("{value:gb}", 4.7) == "4.7"  # no unit: GB already
    assert safe_format("{value:gb} {unit}", 2048, "MB") == "2.0 MB"  # the unit stays the reading's


def test_texts_in_capitals():
    assert safe_format("{value:upper}", "Round report") == "ROUND REPORT"
    assert safe_format("{label:upper}: {value:.0f}", 7, label="CPU temp") == "CPU TEMP: 7"
    assert safe_format("{value:upper}", 4.5) == "4.5"  # nothing to raise in a number


@pytest.mark.parametrize(
    "fmt",
    [
        "{value.__class__}",
        "{value.__class__.__init__.__globals__}",
        "{value[0]}",
        "{secret}",
        "{0}",
        "{value:>100000000}",
        "{value:{unit}}",
    ],
)
def test_untrusted_format_strings_are_refused(fmt):
    with pytest.raises(FormatError):
        safe_format(fmt, 1.0, "x")


def test_type_errors_become_format_errors():
    with pytest.raises(FormatError):
        safe_format("{value:.0f}", "text")
