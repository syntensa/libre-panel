"""The devices service: what is told when drives, controllers and USB devices come and go and
when storage runs out or hot, the messages it makes, and its thread. The computer is made up
(``Scene``), time is by hand, and no Windows is needed."""

import collections
import threading

import pytest
from fake_sensors import wait_for

from libre_panel import i18n
from libre_panel.icons import ICON_NAMES
from libre_panel.plugins import PluginHost
from libre_panel.plugins.host import ServiceHost, apply_options
from libre_panel.services import devices
from libre_panel.services.devices import Devices, Event, Watcher, describe

GB = 1000**3
DEFAULTS = apply_options(Devices.options, {}, "services.devices")


def drive(name="Stick", bus="USB", free=20, total=30, label=""):
    return {"name": name, "label": label, "free": free * GB, "total": total * GB, "bus": bus}


def pad(battery=3, level=3, wireless=True):
    return {"wireless": wireless, "battery": battery, "level": level}


def usb(vid="18D1", pid="4EE1", name="Android Phone"):
    return {"vid": vid, "pid": pid, "name": name, "klass": "WPD", "service": ""}


def disk(free, total=500, temp=None, warn=None, crit=None, name="System SSD"):
    return {"name": name, "free": free * GB, "total": total * GB, "temp": temp,
            "warn": warn, "crit": crit}  # fmt: skip


class Scene:
    """The computer as the readers see it: change ``state`` between two looks."""

    def __init__(self):
        self.state = {"drives": {}, "controllers": {}, "usb": {}, "storage": {}}
        self.reads = collections.Counter()
        self.ignored = None
        self.fail = None  # raised by the next read

    def _read(self, what):
        self.reads[what] += 1
        if self.fail is not None:
            raise self.fail
        return {key: dict(value) for key, value in self.state[what].items()}

    def drives(self):
        return self._read("drives")

    def controllers(self):
        return self._read("controllers")

    def storage(self):
        return self._read("storage")

    def usb(self, ignore):
        self.ignored = ignore
        return self._read("usb")


class Bench:
    """A watcher over a scene, with the time by hand. ``step`` is a look ``seconds`` later and
    gives the events it told."""

    def __init__(self, events=False, **options):
        self.scene, self.told = Scene(), []
        self.watcher = Watcher(
            self.scene, {**DEFAULTS, **options}, self.told.append, frozenset({"1CBE:0092"})
        )
        self.watcher.events = events
        self.now = 1000.0

    def step(self, seconds=0.0, hurry=False):
        self.now += seconds
        if hurry:
            self.watcher.hurry()
        self.watcher.step(self.now)
        told, self.told[:] = list(self.told), []
        return told

    def names(self, *args, **kwargs):
        return [event.what for event in self.step(*args, **kwargs)]


# -- the start ------------------------------------------------------------------------------------


def test_what_is_there_at_the_start_is_taken_in_silently():
    b = Bench()
    b.scene.state.update(
        drives={"E": drive()},
        controllers={0: pad(level=0), 1: pad(battery=1, level=3, wireless=False)},
        usb={"usb-1": usb()},
        storage={"C": disk(100, temp=40)},
    )
    assert b.step() == []  # a controller that has been there for hours is no news, weak or not
    for _ in range(8):
        assert b.step(2) == []
    assert b.scene.ignored == frozenset({"1CBE:0092"})  # the panel is left out by the reader


def test_a_weak_battery_at_the_start_is_not_told_but_one_that_weakens_is():
    b = Bench()
    b.scene.state["controllers"] = {0: pad(level=1), 1: pad(level=3)}
    assert b.step() == []
    b.scene.state["controllers"][1] = pad(level=1)
    assert [e.key for e in b.step(1)] == ["controller:1"]
    b.scene.state["controllers"][0] = pad(level=0)  # worse, but told already
    assert b.step(1) == []


# -- drives ---------------------------------------------------------------------------------------


def test_a_drive_that_comes_and_goes_is_told_with_what_it_is():
    b = Bench()
    b.step()
    b.scene.state["drives"]["E"] = drive("Kingston DataTraveler", "USB", 20, 30)
    [came] = b.step(2)
    assert (came.what, came.key) == ("drive-in", "drive:E")
    assert came.data["letter"] == "E" and came.data["name"] == "Kingston DataTraveler"
    assert (came.data["bus"], came.data["free"], came.data["total"]) == ("USB", 20 * GB, 30 * GB)
    assert b.step(2) == []  # and once
    del b.scene.state["drives"]["E"]
    [went] = b.step(2)
    assert (went.what, went.data["name"]) == ("drive-out", "Kingston DataTraveler")  # as it was


def test_drives_are_looked_at_as_often_as_the_poll_says():
    b = Bench()
    b.step()
    reads = b.scene.reads["drives"]
    b.step(1.0)
    assert b.scene.reads["drives"] == reads  # DRIVE_TICK is 2 s
    b.step(1.1)
    assert b.scene.reads["drives"] == reads + 1
    with_notices = Bench(events=True)
    with_notices.step()
    reads = with_notices.scene.reads["drives"]
    with_notices.step(9.0)
    assert with_notices.scene.reads["drives"] == reads  # Windows tells: only a net every 10 s
    with_notices.step(1.1)
    assert with_notices.scene.reads["drives"] == reads + 1


def test_a_notice_of_windows_brings_the_look_at_once():
    b = Bench(events=True)
    b.step()
    b.scene.state["drives"]["E"] = drive()
    assert b.step(0.1) == []  # the net is ten seconds away
    assert [e.what for e in b.step(0.1, hurry=True)] == ["drive-in"]


def test_four_changes_at_once_are_taken_in_silently():
    b = Bench()
    b.step()
    b.scene.state["drives"] = {letter: drive(letter) for letter in "EFG"}
    assert b.names(2) == ["drive-in"] * 3  # three are told
    b.scene.state["drives"] = {letter: drive(letter) for letter in "HIJK"}  # the computer woke up
    assert b.step(2) == []  # three gone and four come: taken in
    del b.scene.state["drives"]["H"]
    assert [e.what for e in b.step(2)] == ["drive-out"]  # and what is there now is the new stock


# -- controllers ----------------------------------------------------------------------------------


def test_a_controller_is_told_when_it_has_stood_there_a_moment():
    b = Bench()
    b.step()
    b.scene.state["controllers"][0] = pad(level=2)
    assert b.step(0.5) == []
    assert b.step(1.0) == []
    [came] = b.step(1.0)  # two seconds: a wireless one tells its battery by then
    assert (came.what, came.key, came.data["level"]) == ("controller-in", "controller:0", 2)
    assert b.step(1.0) == []


def test_a_controller_that_comes_and_goes_at_once_is_no_news():
    b = Bench()
    b.step()
    b.scene.state["controllers"][1] = pad()
    assert b.step(0.5) == [] and b.watcher.pause == devices.BUSY_TICK  # it settles
    b.scene.state["controllers"] = {}
    assert b.step(0.5) == [] and b.watcher.pause == devices.IDLE_TICK
    assert b.step(5.0) == []


def test_with_windows_notices_a_cable_or_a_known_battery_is_told_after_half_a_second():
    b = Bench(events=True)
    b.step()
    b.scene.state["controllers"][0] = pad(battery=1, level=3, wireless=False)
    b.scene.state["controllers"][1] = pad(battery=None, level=None)  # wireless, no battery yet
    assert b.step(0.3) == [] and b.step(0.3) == []  # seen, and 0.3 s old
    assert [e.key for e in b.step(0.3)] == ["controller:0"]  # 0.6 s
    assert b.step(1.0) == []
    assert [e.key for e in b.step(0.5)] == ["controller:1"]  # the other one waited two seconds


def test_a_controller_that_goes_is_told():
    b = Bench()
    b.scene.state["controllers"] = {2: pad()}
    b.step()
    del b.scene.state["controllers"][2]
    [went] = b.step(1.0)
    assert (went.what, went.data["slot"]) == ("controller-out", 2)


def test_a_battery_that_runs_low_is_told_once_until_it_has_been_charged():
    b = Bench()
    b.scene.state["controllers"] = {0: pad(level=3)}
    b.step()
    b.scene.state["controllers"][0] = pad(level=2)
    assert b.step(1.0) == []
    b.scene.state["controllers"][0] = pad(level=1)
    [low] = b.step(1.0)
    assert (low.what, low.data["level"]) == ("controller-low", 1)
    b.scene.state["controllers"][0] = pad(level=0)
    assert b.step(1.0) == [] and b.step(1.0) == []  # still told: not again
    b.scene.state["controllers"][0] = pad(level=3)  # charged
    assert b.step(1.0) == []
    b.scene.state["controllers"][0] = pad(level=0)
    [empty] = b.step(1.0)
    assert empty.data["level"] == 0  # told anew
    b.scene.state["controllers"][0] = pad(battery=1, level=3, wireless=False)  # on a cable
    assert b.step(1.0) == []


# -- USB devices ----------------------------------------------------------------------------------


def test_a_usb_device_is_told_when_it_has_settled():
    b = Bench()
    b.step()
    b.scene.state["usb"]["phone"] = usb()
    assert b.step(2.0) == []  # it appeared; Windows may still be setting it up
    assert b.watcher.pause == devices.BUSY_TICK
    assert b.step(2.0) == []  # two seconds old
    [came] = b.step(2.0)  # four seconds: USB_WAIT
    assert (came.what, came.key, came.data["name"]) == ("usb-in", "usb:phone", "Android Phone")
    assert b.step(2.0) == [] and b.watcher.pause == devices.IDLE_TICK


def test_with_windows_notices_it_is_told_when_windows_has_gone_quiet():
    b = Bench(events=True)
    b.step()
    b.scene.state["usb"]["phone"] = usb()
    b.watcher.last_notice = b.now  # the first of a dozen notices
    assert b.step(0.3, hurry=True) == []  # seen
    b.watcher.last_notice = b.now  # more come
    assert b.step(0.4) == []  # 0.4 s old: too young (0.6 s)
    assert b.step(0.3) == []  # old enough, but Windows spoke 0.7 s ago
    [came] = b.step(0.4)  # more than a second of quiet
    assert came.what == "usb-in"


def test_a_usb_device_never_settling_is_told_after_four_seconds_anyway():
    b = Bench(events=True)
    b.step()
    b.scene.state["usb"]["phone"] = usb()
    b.step(0.1, hurry=True)  # seen
    for _ in range(3):
        b.now += 1.0
        b.watcher.last_notice = b.now  # the notices never stop
        b.watcher.step(b.now)
        assert b.told == []
    b.now += 1.0
    b.watcher.last_notice = b.now
    b.watcher.step(b.now)  # four seconds
    assert [e.what for e in b.told] == ["usb-in"]


def test_a_usb_device_that_goes_again_at_once_is_no_news_and_one_that_stays_is_told_leaving():
    b = Bench()
    b.step()
    b.scene.state["usb"]["flash"] = usb(name="Flash")
    b.step(2.0)
    del b.scene.state["usb"]["flash"]
    assert b.step(2.0) == [] and b.step(5.0) == []  # never told, so not told leaving
    b.scene.state["usb"]["phone"] = usb()
    b.step(2.0)
    assert b.step(2.0) == []
    assert [e.what for e in b.step(2.0)] == ["usb-in"]
    del b.scene.state["usb"]["phone"]
    [went] = b.step(2.0)
    assert (went.what, went.data["name"]) == ("usb-out", "Android Phone")


def test_a_usb_stick_is_told_as_a_drive_and_not_again_as_a_usb_device():
    b = Bench()
    b.step()
    b.scene.state["drives"]["E"] = drive()
    b.scene.state["usb"]["stick"] = usb("0951", "1666", "DataTraveler")
    assert b.names(2.0) == ["drive-in"]  # the drive is looked at first
    assert b.step(2.0) == [] and b.step(2.0) == [] and b.step(2.0) == []  # and no second message
    del b.scene.state["drives"]["E"], b.scene.state["usb"]["stick"]
    assert b.names(2.0) == ["drive-out"]  # leaving the same


def test_with_windows_notices_the_stick_is_one_message_too():
    b = Bench(events=True)
    b.step()
    b.scene.state["drives"]["E"] = drive()
    b.scene.state["usb"]["stick"] = usb("0951", "1666", "DataTraveler")
    b.watcher.last_notice = b.now
    assert b.names(0.2, hurry=True) == ["drive-in"]
    for _ in range(6):
        assert b.step(0.5) == []


def test_a_controller_told_just_before_is_not_told_again_as_usb():
    b = Bench()
    b.step()
    b.scene.state["controllers"][0] = pad()
    b.scene.state["usb"]["pad"] = usb("1234", "5678", "Game Pad")
    names = []
    for _ in range(5):
        names += b.names(1.0)
    assert names == ["controller-in"]


def test_four_changes_at_once_in_usb_are_taken_in_silently_and_cancel_what_waited():
    b = Bench()
    b.step()
    b.scene.state["usb"]["one"] = usb(name="One")
    b.step(2.0)  # waiting to settle
    b.scene.state["usb"].update({n: usb(name=n) for n in ("a", "b", "c", "d")})  # the hubs reset
    assert b.step(2.0) == [] and b.step(5.0) == []
    del b.scene.state["usb"]["a"]
    [went] = b.step(5.0)  # but what is there now counts as known
    assert went.key == "usb:a"


def test_the_usb_devices_are_asked_without_the_panel_and_the_listed_ones():
    seen = []

    class Reader(Scene):
        def usb(self, ignore):
            seen.append(ignore)
            return {}

    watcher = Watcher(Reader(), DEFAULTS, lambda event: None, frozenset({"1CBE:0092", "AAAA:0001"}))
    watcher.step(1000.0)
    assert seen == [frozenset({"1CBE:0092", "AAAA:0001"})]


# -- storage ---------------------------------------------------------------------------------------


def test_a_drive_that_runs_full_is_told_once_and_again_when_it_ran_empty_and_filled_up():
    b = Bench()
    b.scene.state["storage"] = {"C": disk(100)}
    assert b.step() == []
    b.scene.state["storage"]["C"] = disk(24)  # less than the larger of 25 GB and 5 % (25 GB of 500)
    assert b.step(59.0) == []  # looked at once a minute
    [full] = b.step(2.0)
    assert (full.what, full.key, full.data["critical"]) == ("space", "storage:C", False)
    assert b.step(61.0) == []  # told
    b.scene.state["storage"]["C"] = disk(29)  # not yet 20 % above the limit
    assert b.step(61.0) == [] and b.step(61.0) == []
    b.scene.state["storage"]["C"] = disk(31)  # above it: armed again
    b.step(61.0)
    b.scene.state["storage"]["C"] = disk(20)
    assert [e.what for e in b.step(61.0)] == ["space"]


def test_a_drive_nearly_empty_is_critical():
    b = Bench()
    b.scene.state["storage"] = {"C": disk(8)}  # a third of 25 GB is 8.3
    [full] = b.step()
    assert full.data["critical"] is True
    b2 = Bench()
    b2.scene.state["storage"] = {"C": disk(9)}
    assert b2.step()[0].data["critical"] is False


def test_the_limit_for_free_space_is_set_by_the_options():
    big = Bench(min_free_gb=100.0, min_free_percent=1.0)
    big.scene.state["storage"] = {"C": disk(90)}
    assert big.names() == ["space"]
    percent = Bench(min_free_gb=1.0, min_free_percent=50.0)
    percent.scene.state["storage"] = {"C": disk(200, total=500)}
    assert percent.names() == ["space"]  # 40 % free is under the 50 %


def test_a_drive_that_runs_hot_is_told_at_its_own_limit_and_again_when_it_is_critical():
    b = Bench()
    b.scene.state["storage"] = {"C": disk(100, temp=60.0, warn=70.0, crit=85.0)}
    assert b.step() == []
    b.scene.state["storage"]["C"] = disk(100, temp=72.0, warn=70.0, crit=85.0)
    [hot] = b.step(61.0)
    assert (hot.what, hot.key, hot.data["critical"], hot.data["limit"]) == (
        "heat",
        "ssdtemp:C",
        False,
        70.0,
    )
    b.scene.state["storage"]["C"] = disk(100, temp=80.0, warn=70.0, crit=85.0)
    assert b.step(61.0) == []  # as bad as before
    b.scene.state["storage"]["C"] = disk(100, temp=90.0, warn=70.0, crit=85.0)
    [worse] = b.step(61.0)
    assert worse.data["critical"] is True and worse.data["temp"] == 90.0
    b.scene.state["storage"]["C"] = disk(100, temp=69.0, warn=70.0, crit=85.0)
    assert b.step(61.0) == []  # under the limit, but not 5 degrees under
    b.scene.state["storage"]["C"] = disk(100, temp=64.0, warn=70.0, crit=85.0)
    assert b.step(61.0) == []  # armed again
    b.scene.state["storage"]["C"] = disk(100, temp=71.0, warn=70.0, crit=85.0)
    assert b.names(61.0) == ["heat"]


def test_the_limit_for_heat_is_the_options_else_the_drives_else_seventy():
    own = Bench(ssd_warn_c=60.0)
    own.scene.state["storage"] = {"C": disk(100, temp=61.0, warn=70.0, crit=85.0)}
    assert own.step()[0].data["limit"] == 60.0
    nameless = Bench()
    nameless.scene.state["storage"] = {"C": disk(100, temp=69.0)}
    assert nameless.step() == []
    nameless.scene.state["storage"]["C"] = disk(100, temp=71.0)
    assert nameless.step(61.0)[0].data["limit"] == devices.DEFAULT_HEAT_C == 70.0


def test_a_drive_that_does_not_tell_its_temperature_or_size_is_no_trouble():
    b = Bench()
    b.scene.state["storage"] = {"C": disk(100, temp=None), "D": {**disk(0), "total": 0}}
    assert b.step() == []


def test_a_drive_that_vanishes_is_forgotten_so_that_it_is_told_when_it_comes_back_full():
    b = Bench()
    b.scene.state["storage"] = {"C": disk(5)}
    assert b.names() == ["space"]
    b.scene.state["storage"] = {}
    b.step(61.0)
    b.scene.state["storage"] = {"C": disk(5)}
    assert b.names(61.0) == ["space"]


# -- what can be switched off ----------------------------------------------------------------------


@pytest.mark.parametrize("what", ["drives", "controllers", "usb", "storage"])
def test_a_part_that_is_off_is_not_even_looked_at(what):
    b = Bench(**{what: False})
    b.scene.state["storage"] = {"C": disk(1)}
    told = b.step()
    b.step(61.0)
    assert b.scene.reads[what] == 0
    assert all(
        b.scene.reads[other] > 0
        for other in ("drives", "controllers", "usb", "storage")
        if other != what
    )
    assert (what == "storage") == (not told)


def test_a_part_turned_off_comes_back_with_a_new_stock():
    b = Bench()
    b.step()
    b.watcher.options = {**b.watcher.options, "drives": False}
    b.step(2.0)
    b.scene.state["drives"]["E"] = drive()
    b.step(2.0)
    b.watcher.options = {**b.watcher.options, "drives": True}
    assert b.step(2.0) == []  # what was plugged in meanwhile is the stock, not news


# -- the messages ----------------------------------------------------------------------------------


def toast(event):
    """The message ``describe`` makes, through the real host."""
    host = PluginHost()
    ServiceHost(host, "devices").notify(**describe(event))
    [made] = host.take_toasts()
    return made


def test_a_drive_plugged_in_says_what_it_is_and_how_full_it_is():
    came = toast(
        Event(
            "drive-in", "drive:E", {"letter": "E", **drive("Kingston DataTraveler", "USB", 20, 30)}
        )
    )
    assert came.text == "Kingston DataTraveler · 20 GB free of 30 GB"
    assert came.payload["label"] == "USB drive E:"
    assert came.payload["progress"] == pytest.approx(10 / 30)
    assert (came.kind, came.rank, came.key, came.icon, came.seconds, came.level) == (
        "device", 1, "drive:E", "stick", devices.PLUGGED_S, "info",
    )  # fmt: skip
    assert came.service == "devices"


def test_a_drive_without_a_name_is_named_by_its_label_then_its_letter():
    base = {"letter": "F", **drive("", "SD", 5, 64, label="PHOTOS")}
    assert toast(Event("drive-in", "drive:F", base)).text.startswith("PHOTOS · ")
    nameless = {**base, "label": ""}
    assert toast(Event("drive-in", "drive:F", nameless)).text.startswith("Drive F: · ")
    assert toast(Event("drive-in", "drive:F", nameless)).payload["label"] == "SD drive F:"


def test_a_drive_taken_out_and_a_drive_without_a_size():
    gone = toast(Event("drive-out", "drive:E", {"letter": "E", **drive("Stick")}))
    assert gone.text == "Stick removed" and gone.seconds == devices.UNPLUGGED_S
    assert gone.payload == {"label": "USB drive E:"} and gone.key == "drive:E"  # replaces the first
    empty = toast(Event("drive-in", "drive:E", {"letter": "E", **drive("Stick", total=0, free=0)}))
    assert empty.text == "Stick" and "progress" not in empty.payload


def test_controllers_say_who_and_how_charged():
    wired = toast(Event("controller-in", "controller:0", {"slot": 0, **pad(1, 3, False)}))
    assert wired.text == "Connected · Wired" and wired.payload == {"label": "Controller · player 1"}
    full = toast(Event("controller-in", "controller:1", {"slot": 1, **pad(3, 3)}))
    assert full.text == "Connected · Battery full" and full.payload["progress"] == 1.0
    assert full.icon == "gamepad" and full.payload["label"] == "Controller · player 2"
    unknown = toast(Event("controller-in", "controller:2", {"slot": 2, **pad(None, None)}))
    assert unknown.text == "Connected" and "progress" not in unknown.payload
    out = toast(Event("controller-out", "controller:1", {"slot": 1}))
    assert (out.text, out.seconds, out.key) == ("Disconnected", devices.UNPLUGGED_S, "controller:1")


def test_a_weak_battery_is_a_warning_of_higher_rank():
    low = toast(Event("controller-low", "controller:0", {"slot": 0, **pad(2, 1)}))
    assert (low.text, low.level, low.rank, low.kind) == ("Battery low", "warning", 2, "device")
    assert low.payload["progress"] == pytest.approx(1 / 3) and low.key == "controller:0"
    empty = toast(Event("controller-low", "controller:0", {"slot": 0, **pad(2, 0)}))
    assert (empty.text, empty.level, empty.payload["progress"]) == ("Battery empty", "error", 0.0)


def test_usb_devices_are_phones_by_their_maker_and_other_devices_by_their_name():
    phone = toast(Event("usb-in", "usb:x", {"id": "x", **usb("18D1", "4EE1", "Android Phone")}))
    assert (phone.text, phone.icon, phone.payload["label"]) == (
        "Android Phone",
        "phone",
        "Phone · Google",
    )
    other = toast(Event("usb-in", "usb:y", {"id": "y", **usb("046D", "C31C", "USB Keyboard")}))
    assert (other.icon, other.payload["label"], other.rank) == ("usb", "USB device", 1)
    nameless = toast(Event("usb-in", "usb:z", {"id": "z", **usb("1A2B", "3C4D", "")}))
    assert nameless.text == "1A2B:3C4D"
    gone = toast(Event("usb-out", "usb:y", {"id": "y", **usb("046D", "C31C", "USB Keyboard")}))
    assert (gone.text, gone.seconds, gone.key) == (
        "USB Keyboard disconnected",
        devices.UNPLUGGED_S,
        "usb:y",
    )


def test_storage_warnings_are_warnings_like_those_of_the_notices():
    full = toast(Event("space", "storage:C", {"letter": "C", "critical": False, **disk(20)}))
    assert (full.kind, full.rank, full.icon, full.level) == ("warning", 2, "disk", "warning")
    assert full.text == "Only 20 GB free" and full.payload["label"] == "Drive C: · System SSD"
    assert full.payload["progress"] == pytest.approx(0.96) and full.key == "storage:C"
    critical = toast(Event("space", "storage:C", {"letter": "C", "critical": True, **disk(5)}))
    assert critical.level == "error"
    hot = toast(Event("heat", "ssdtemp:C", {"letter": "C", "critical": False, "limit": 70.0,
                                            **disk(100, temp=75.4, crit=85.0)}))  # fmt: skip
    assert (hot.kind, hot.icon, hot.text) == ("warning", "ssd", "75 °C · limit 70 °C")
    assert hot.payload["label"] == "SSD C: · System SSD" and hot.payload[
        "progress"
    ] == pytest.approx(75.4 / 85)
    unnamed = toast(Event("heat", "ssdtemp:D", {"letter": "D", "critical": True, "limit": 70.0,
                                                **disk(100, temp=99.0, name="")}))  # fmt: skip
    assert unnamed.payload["label"] == "SSD D:" and unnamed.level == "error"
    assert unnamed.payload["progress"] == 1.0  # past the scale


def test_every_icon_a_message_names_exists():
    events = [
        Event("drive-in", "k", {"letter": "E", **drive()}),
        Event("controller-in", "k", {"slot": 0, **pad()}),
        Event("usb-in", "k", {"id": "k", **usb()}),
        Event("usb-in", "k", {"id": "k", **usb("046D", "C31C", "Keyboard")}),
        Event("space", "k", {"letter": "C", "critical": False, **disk(1)}),
        Event("heat", "k", {"letter": "C", "critical": False, "limit": 70.0, **disk(1, temp=80.0)}),
    ]
    assert {toast(event).icon for event in events} == {
        "stick",
        "gamepad",
        "phone",
        "usb",
        "disk",
        "ssd",
    }
    assert {"stick", "gamepad", "phone", "usb", "disk", "ssd"} <= set(ICON_NAMES)
    with pytest.raises(ValueError, match="unknown"):
        describe(Event("nonsense", "k"))


def test_the_messages_follow_the_language():
    i18n.set_language("de")
    came = toast(Event("drive-in", "drive:E", {"letter": "E", **drive("Stick", "USB", 20, 30)}))
    assert (
        came.text == "Stick · 20 GB frei von 30 GB" and came.payload["label"] == "USB-Laufwerk E:"
    )
    pad_in = toast(Event("controller-in", "controller:0", {"slot": 0, **pad(3, 2)}))
    assert (
        pad_in.text == "Verbunden · Akku mittel"
        and pad_in.payload["label"] == "Controller · Spieler 1"
    )
    assert (
        toast(Event("usb-out", "usb:y", {"id": "y", **usb(name="Handy")})).text == "Handy getrennt"
    )
    assert (
        toast(Event("usb-in", "usb:y", {"id": "y", **usb()})).payload["label"] == "Handy · Google"
    )
    low = toast(Event("space", "storage:C", {"letter": "C", "critical": False, **disk(20)}))
    assert low.text == "Nur noch 20 GB frei" and low.payload["label"].startswith("Laufwerk C:")


def test_sizes_read_as_drives_are_sold():
    assert [devices.size_text(n) for n in (512e6, 4.5e9, 29.8e9, 999e9, 1.0e12, 2.5e12)] == [
        "512 MB", "4.5 GB", "30 GB", "999 GB", "1.0 TB", "2.5 TB",
    ]  # fmt: skip


# -- the options -----------------------------------------------------------------------------------


def test_the_panels_own_ids_are_always_left_out():
    ids = devices.panel_ids()
    assert "1CBE:0092" in ids and "1A86:CA21" in ids and "0525:A4A7" in ids  # USB and serial panels
    assert all(len(i) == 9 and i == i.upper() for i in ids)


def test_the_list_of_usb_ids_to_leave_out_is_read_and_a_wrong_entry_is_reported():
    problems = []
    found = devices.parse_ignored(
        ["1a86:7523", " ABCD:0001 ", "nonsense", 5, "1234:12345"], problems.append
    )
    assert found == {"1A86:7523", "ABCD:0001"}
    assert len(problems) == 3 and "nonsense" in problems[0] and "VID:PID" in problems[0]


# -- the service -----------------------------------------------------------------------------------


class FakeNotifier:
    """Windows' notices by hand: ``fire`` is a device interface that came or went."""

    def __init__(self, works=True):
        self.works, self.callback, self.stopped = works, None, threading.Event()

    def start(self, on_change):
        self.callback = on_change
        return self.works

    def stop(self):
        self.stopped.set()

    def fire(self):
        self.callback()


@pytest.fixture
def service(monkeypatch):
    """A device service over a made-up computer, quick paces, a host to read toasts from."""
    for name, seconds in {"WAKE_SETTLE": 0.01, "IDLE_TICK": 0.02, "BUSY_TICK": 0.01}.items():
        monkeypatch.setattr(devices, name, seconds)
    made = []

    def build(scene=None, notifier=None, **options):
        host = PluginHost()
        scene = scene or Scene()
        options = apply_options(Devices.options, options, "services.devices")
        notifier = notifier if notifier is not None else FakeNotifier()
        made.append(
            (Devices(ServiceHost(host, "devices"), options, readers=scene, notifier=notifier), host)
        )
        return made[-1][0], host, scene, notifier

    yield build
    for each, host in made:
        each.stop()
        host.close()


def collect(host, count):
    """The toasts that come, as soon as ``count`` of them are there."""
    got = []
    assert wait_for(lambda: got.extend(host.take_toasts()) or len(got) >= count)
    return got


def test_the_service_tells_what_the_computer_does_while_it_runs(service):
    svc, host, scene, notifier = service()
    scene.state["drives"] = {"D": drive("Existing")}
    svc.start()
    assert wait_for(lambda: scene.reads["drives"] >= 1 and scene.reads["storage"] >= 1)
    assert host.take_toasts() == [] and svc.watcher.events is True  # what was there is no news
    scene.state["drives"]["E"] = drive("New Stick")
    notifier.fire()  # Windows tells: it is looked at once, not at the next net
    [came] = collect(host, 1)
    assert came.text.startswith("New Stick") and came.kind == "device" and came.icon == "stick"
    del scene.state["drives"]["E"]
    notifier.fire()
    [gone] = collect(host, 1)
    assert gone.text == "New Stick removed" and gone.key == came.key  # it replaces the first


def test_without_notices_from_windows_it_polls_and_says_so(service, caplog, monkeypatch):
    monkeypatch.setattr(devices, "DRIVE_TICK", 0.02)
    svc, host, scene, notifier = service(notifier=FakeNotifier(works=False))
    with caplog.at_level("INFO", logger="libre_panel.service.devices"):
        svc.start()
        assert wait_for(lambda: scene.reads["drives"] >= 1)
        scene.state["drives"]["E"] = drive()
        [came] = collect(host, 1)  # found by the poll, nothing woke it
    assert came.key == "drive:E" and svc.watcher.events is False
    assert "no notices from Windows" in caplog.text


def test_the_service_stops_and_lets_go_of_windows_notices(service):
    svc, host, scene, notifier = service()
    svc.start()
    assert wait_for(lambda: scene.reads["storage"] >= 1)
    thread = svc._thread
    svc.stop()
    assert not thread.is_alive() and notifier.stopped.is_set()
    svc.stop()  # twice is fine


def test_a_look_that_fails_is_said_once_and_the_service_goes_on(service, caplog):
    svc, host, scene, notifier = service()
    scene.fail = OSError("no access")
    with caplog.at_level("INFO", logger="libre_panel.service.devices"):
        svc.start()
        assert wait_for(lambda: scene.reads["controllers"] >= 3)  # looked at again and again
        scene.fail = None
        assert wait_for(lambda: "works again" in caplog.text)
    assert caplog.text.count("looking at devices failed") == 1 and "no access" in caplog.text
    assert svc._thread.is_alive()


def test_where_this_is_not_windows_the_service_runs_idle_and_says_so(caplog, monkeypatch):
    monkeypatch.setattr(devices.sys, "platform", "linux")
    host = PluginHost()
    svc = Devices(
        ServiceHost(host, "devices"), apply_options(Devices.options, {}, "services.devices")
    )
    assert svc.readers is None and svc.notifier is None
    with caplog.at_level("WARNING", logger="libre_panel.service.devices"):
        svc.start()
    assert "Windows only" in caplog.text and svc._thread is None
    svc.stop()
    host.close()


def test_on_windows_the_service_takes_the_systems_readers(monkeypatch):
    monkeypatch.setattr(devices.sys, "platform", "win32")
    host = PluginHost()
    svc = Devices(
        ServiceHost(host, "devices"), apply_options(Devices.options, {}, "services.devices")
    )
    assert isinstance(svc.readers, devices.windows.Readers)
    assert isinstance(svc.notifier, devices.windows.ChangeNotifier)
    host.close()


def test_the_usb_ids_to_leave_out_join_the_panels_and_a_wrong_one_is_logged(caplog):
    host = PluginHost()
    options = apply_options(
        Devices.options, {"usb_ignore": ["1a86:7523", "wrong"]}, "services.devices"
    )
    with caplog.at_level("WARNING", logger="libre_panel.service.devices"):
        svc = Devices(
            ServiceHost(host, "devices"), options, readers=Scene(), notifier=FakeNotifier()
        )
    assert {"1A86:7523", "1CBE:0092"} <= svc.watcher.ignore
    assert "wrong" in caplog.text
    host.close()


def test_the_options_are_documented_types_with_defaults():
    assert DEFAULTS == {
        "drives": True, "usb": True, "controllers": True, "storage": True, "usb_ignore": [],
        "min_free_gb": 25.0, "min_free_percent": 5.0, "ssd_warn_c": 0.0,
    }  # fmt: skip
