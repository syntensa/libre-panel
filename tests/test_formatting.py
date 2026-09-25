import pytest

from libre_panel.render.formatting import FormatError, human_bytes, human_duration, safe_format


def test_basic_format():
    assert safe_format("{value:.0f}{unit}", 45.6, "°C") == "46°C"
    assert safe_format("{label}: {value}", 3, label="Fans") == "Fans: 3"


def test_extra_specs():
    assert safe_format("{value:bytes}/s", 2_400_000) == "2.4 MB/s"
    assert safe_format("{value:duration}", 3 * 86400 + 5 * 3600) == "3d 5h"
    assert human_bytes(512) == "512 B"
    assert human_duration(59) == "0m"


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
