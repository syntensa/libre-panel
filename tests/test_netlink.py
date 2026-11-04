"""How the PC is connected (sensors/netlink.py): the default route's interface, cable or Wi-Fi,
the speed and the signal, as readings (net.link ...), the icon that follows them, and the NET
card that shows them."""

import numpy as np
import pytest

from libre_panel import i18n
from libre_panel.icons import ICON_NAMES, draw_icon, link_icon
from libre_panel.sensors import base, netlink, psutil_provider
from libre_panel.sensors.netlink import LAN, WLAN, Link

ROUTE = """Iface\tDestination\tGateway \tFlags\tRefCnt\tUse\tMetric\tMask\t\tMTU\tWindow\tIRTT
eth0\t0000A8C0\t00000000\t0001\t0\t0\t100\t00FFFFFF\t0\t0\t0
wlan0\t00000000\t0100A8C0\t0003\t0\t0\t600\t00000000\t0\t0\t0
eth0\t00000000\t0100A8C0\t0003\t0\t0\t100\t00000000\t0\t0\t0
"""

WIRELESS = """Inter-| sta-|   Quality        |   Discarded packets               | Missed | WE
 face | tus | link level noise |  nwid  crypt   frag  retry   misc | beacon | 22
wlan0: 0000   49.  -61.  -256        0      0      0      0     12        0
"""


def test_the_default_route_with_the_lowest_metric_is_the_connection():
    assert netlink.default_interface(ROUTE) == "eth0"  # (the Wi-Fi's metric is higher)
    assert netlink.default_interface(ROUTE.splitlines()[0]) is None  # no default route


def test_the_wifi_quality_is_a_share_of_seventy():
    assert netlink.wireless_quality(WIRELESS, "wlan0") == pytest.approx(70.0)
    assert netlink.wireless_quality(WIRELESS, "wlan1") is None


@pytest.mark.parametrize(("mbit", "said"), [(100, "100 Mbit/s"), (866.7, "867 Mbit/s"),
                                            (1000, "1 Gbit/s"), (2500, "2.5 Gbit/s")])  # fmt: skip
def test_a_speed_as_one_says_it(mbit, said):
    assert netlink.speed_text(mbit) == said


def fake_linux(monkeypatch, tmp_path, *, wireless, speed="1000", rate=None):
    proc, sys_net = tmp_path / "proc", tmp_path / "sys"
    (proc / "net").mkdir(parents=True)
    name = "wlan0" if wireless else "eth0"
    route = ROUTE
    if wireless:  # (the Wi-Fi alone has the default route)
        route = "\n".join(line for line in route.splitlines() if not line.startswith("eth0"))
    (proc / "net" / "route").write_text(route)
    (proc / "net" / "wireless").write_text(WIRELESS)
    (sys_net / name).mkdir(parents=True)
    if wireless:
        (sys_net / name / "wireless").mkdir()
    (sys_net / name / "speed").write_text(speed + "\n")
    monkeypatch.setattr(netlink, "_PROC", proc)
    monkeypatch.setattr(netlink, "_SYS", sys_net)
    monkeypatch.setattr(netlink, "_iw_rate", lambda _name: rate)
    monkeypatch.setattr(netlink.sys, "platform", "linux")


def test_linux_a_cable_and_its_speed(monkeypatch, tmp_path):
    fake_linux(monkeypatch, tmp_path, wireless=False)
    assert netlink.read() == Link(LAN, 1000.0, None, "eth0")


def test_linux_a_cable_without_a_speed(monkeypatch, tmp_path):
    fake_linux(monkeypatch, tmp_path, wireless=False, speed="-1")  # (no link negotiated)
    assert netlink.read() == Link(LAN, None, None, "eth0")


def test_linux_wifi_with_its_signal_and_rate(monkeypatch, tmp_path):
    fake_linux(monkeypatch, tmp_path, wireless=True, rate=866.7)
    assert netlink.read() == Link(WLAN, 866.7, pytest.approx(70.0), "wlan0")


def test_the_rate_from_iw(monkeypatch):
    class Done:
        stdout = "Connected to aa:bb (on wlan0)\n\tsignal: -52 dBm\n\ttx bitrate: 866.7 MBit/s"

    monkeypatch.setattr(netlink.shutil, "which", lambda _name: "/usr/sbin/iw")
    monkeypatch.setattr(netlink.subprocess, "run", lambda *a, **k: Done())
    assert netlink._iw_rate("wlan0") == 866.7
    monkeypatch.setattr(netlink.shutil, "which", lambda _name: None)
    assert netlink._iw_rate("wlan0") is None  # (no iw: no rate, the rest stays)


def test_a_reading_that_fails_is_no_reading(monkeypatch):
    def broken():
        raise OSError("no /proc")

    monkeypatch.setattr(netlink, "_linux", broken)
    monkeypatch.setattr(netlink.sys, "platform", "linux")
    assert netlink.read() is None
    monkeypatch.setattr(netlink.sys, "platform", "darwin")
    assert netlink.read() is None


def test_the_readings_in_the_panels_language():
    i18n.set_language("de")
    try:
        got = psutil_provider.link_readings(Link(WLAN, 866.7, 64.0))
        assert got["net.link"].value == "WLAN" and got["net.link.kind"].value == "wlan"
        assert got["net.link.text"].value == "WLAN · 867 Mbit/s"
        assert (got["net.wifi.signal"].value, got["net.link.speed"].unit) == (64.0, "Mbit/s")
        cable = psutil_provider.link_readings(Link(LAN))
        assert cable["net.link.text"].value == "LAN" and "net.wifi.signal" not in cable
    finally:
        i18n.set_language("en")
    assert psutil_provider.link_readings(None) == {}


def test_the_connection_is_read_only_while_shown_and_seldom(monkeypatch):
    calls = []
    monkeypatch.setattr(netlink, "read", lambda: calls.append(1) or Link(LAN, 1000.0))
    clock = [100.0]
    monkeypatch.setattr(psutil_provider.time, "monotonic", lambda: clock[0])
    monkeypatch.setattr(psutil_provider, "_local_ip", lambda: "")
    monkeypatch.setattr(base, "_WANTED", {})  # (no other test's theme on show)
    provider = psutil_provider.PsutilProvider()
    out = {}
    provider._network(out)
    assert calls == [] and "net.link" not in out  # no theme shows it

    class Owner:  # (a renderer, whose theme shows the connection)
        pass

    owner = Owner()
    base.want(owner, {"net.link.text"})
    try:
        provider._network(out)
        clock[0] += 5
        provider._network(out)
        assert len(calls) == 1 and out["net.link.text"].value == "LAN · 1 Gbit/s"
        clock[0] += psutil_provider.LINK_EVERY_S
        provider._network(out)
        assert len(calls) == 2
    finally:
        base.want(owner, set())


@pytest.mark.parametrize(("kind", "signal", "icon"), [
    ("lan", None, "ethernet"), ("wlan", 90.0, "wifi"), ("wlan", 50.0, "wifi-2"),
    ("wlan", 10.0, "wifi-1"), ("wlan", None, "wifi"), (None, None, "network"),
])  # fmt: skip
def test_the_link_icon_follows_the_connection(kind, signal, icon):
    assert link_icon(kind, signal) == icon and icon in ICON_NAMES


def test_the_wifi_icons_light_as_many_arcs_as_the_signal_is_strong():
    """The arcs not lit stay, faint: the symbol's size shows what one of three means."""
    strong, weak = (draw_icon(name, 48, (255, 255, 255, 255)) for name in ("wifi", "wifi-1"))
    assert all(abs(x - y) <= 1 for x, y in zip(strong.getbbox(), weak.getbbox(), strict=True))
    lit = [int((np.asarray(icon.getchannel("A")) > 200).sum()) for icon in (strong, weak)]
    assert lit[0] > lit[1] * 2


def test_the_renderer_draws_the_connections_icon():
    from libre_panel.render.renderer import Renderer
    from libre_panel.sensors.base import Reading, Snapshot

    renderer = Renderer.__new__(Renderer)
    widget = {"icon": "link", "sensor": ""}

    def snapshot(**values):
        return Snapshot(readings={k.replace("_", "."): Reading(k.replace("_", "."), v)
                                  for k, v in values.items()})  # fmt: skip

    assert renderer._icon_name(widget, snapshot(net_link_kind="lan")) == "ethernet"
    wifi = snapshot(net_link_kind="wlan", net_wifi_signal=20.0)
    assert renderer._icon_name(widget, wifi) == "wifi-1"


def test_the_net_card_says_how_the_pc_is_connected():
    from libre_panel.render.renderer import Renderer
    from libre_panel.theme.model import find_theme, load_theme

    renderer = Renderer(load_theme(find_theme("lumen-1920x720")), preview=True)
    try:
        parts = {w["id"]: w for w in renderer.widgets if str(w.get("id", "")).startswith("net/")}
    finally:
        renderer.close()
    text = next(w for name, w in parts.items() if name.endswith("-link-text"))
    icon = next(w for name, w in parts.items() if name.endswith("-link"))
    assert text["sensor"] == "net.link.text" and text["hide_if_missing"]
    assert icon["icon"] == "link" and "net.link.kind" in icon["needs"]
    assert icon["x"] < text["x"] and text["align"] == "left"  # at the top left: symbol, words
