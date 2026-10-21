"""The UPS through Network UPS Tools: upsd's text protocol, and the readings of its values."""

import socket
import threading

import pytest
from fake_sensors import wait_for

from libre_panel.sensors.base import available_providers
from libre_panel.sensors.nut import NutProvider, readings

VALUES = {
    "battery.charge": "87",
    "battery.runtime": "1260",
    "ups.load": "23",
    "ups.realpower.nominal": "900",
    "input.voltage": "231.5",
    "ups.status": "OL CHRG",
}


class Upsd:
    """A upsd on a free port of this PC, with one UPS ("box") and ``values``."""

    def __init__(self, values, ups="box", wants_login=False):
        self.values, self.ups, self.wants_login = dict(values), ups, wants_login
        self.heard = []
        self.server = socket.create_server(("127.0.0.1", 0))
        self.port = self.server.getsockname()[1]
        threading.Thread(target=self._serve, daemon=True).start()

    def _serve(self):
        while True:
            try:
                conn, _ = self.server.accept()
            except OSError:
                return
            with conn, conn.makefile("rwb") as talk:
                logged = not self.wants_login
                for raw in talk:
                    line = raw.decode().strip()
                    self.heard.append(line)
                    out = self._answer(line, logged)
                    if line.startswith("PASSWORD"):
                        logged = line == "PASSWORD secret"
                    if out is None:
                        break
                    talk.write(out.encode())
                    talk.flush()

    def _answer(self, line, logged):
        if line == "LOGOUT":
            return None
        if line.startswith(("USERNAME", "PASSWORD")):
            return "OK\n"
        if line == "LIST UPS":
            return f'BEGIN LIST UPS\nUPS {self.ups} "The box"\nEND LIST UPS\n'
        if line == f"LIST VAR {self.ups}":
            if not logged:
                return "ERR ACCESS-DENIED\n"
            rows = "".join(f'VAR {self.ups} {k} "{v}"\n' for k, v in self.values.items())
            return f"BEGIN LIST VAR {self.ups}\n{rows}END LIST VAR {self.ups}\n"
        return "ERR UNKNOWN-UPS\n"

    def close(self):
        self.server.close()


@pytest.fixture
def upsd():
    made = []

    def make(*args, **kwargs):
        made.append(Upsd(*args, **kwargs))
        return made[-1]

    yield make
    for each in made:
        each.close()


def test_the_values_of_a_ups_are_read_as_readings():
    got = readings(VALUES)
    assert got["ups.charge"].value == 87 and got["ups.charge"].unit == "%"
    assert got["ups.runtime"].value == 21 and got["ups.runtime"].unit == "min"
    assert got["ups.load"].value == 23
    assert got["ups.power"].value == 207  # of the nominal 900 W, by the load
    assert got["ups.input"].value == 231.5 and got["ups.input"].unit == "V"
    assert got["ups.on_battery"].value == 0 and got["ups.status"].value == "On mains"
    off = readings({**VALUES, "ups.status": "OB DISCHRG", "ups.realpower": "310"})
    assert off["ups.on_battery"].value == 1 and off["ups.status"].value == "On battery"
    assert off["ups.power"].value == 310  # as the UPS says, where it says it
    assert readings({**VALUES, "ups.status": "OB LB"})["ups.status"].value == "Battery low"
    assert readings({"ups.status": ""}) == {}  # what it does not say is left out


def test_the_first_ups_upsd_knows_is_asked_and_its_readings_kept(upsd):
    server = upsd(VALUES)
    source = NutProvider({"port": server.port}, start=False)
    assert source.read() == {}  # nothing yet
    source.fetch()
    assert source.read()["ups.charge"].value == 87
    assert wait_for(lambda: server.heard == ["LIST UPS", "LIST VAR box", "LOGOUT"])
    named = NutProvider({"port": server.port, "ups": "box"}, start=False)
    named.fetch()
    assert wait_for(lambda: server.heard[3:] == ["LIST VAR box", "LOGOUT"]) and named.read()


def test_a_login_where_upsd_wants_one_and_a_ups_that_does_not_answer(upsd, caplog):
    server = upsd(VALUES, wants_login=True)
    source = NutProvider({"port": server.port, "ups": "box", "username": "panel",
                          "password": "secret"}, start=False)  # fmt: skip
    source.fetch()
    assert server.heard[:2] == ["USERNAME panel", "PASSWORD secret"] and source.read()
    refused = NutProvider({"port": server.port, "ups": "box"}, start=False)
    refused.fetch()
    refused.fetch()
    assert refused.read() == {}
    assert caplog.text.count("does not answer: ACCESS-DENIED") == 1  # said once
    server.close()
    source.fetch()  # gone: the last values hold a while
    assert source.read()["ups.charge"].value == 87


def test_nut_is_a_source_to_choose():
    assert "nut" in available_providers()
