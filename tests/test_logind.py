"""Linux: the panel sleeps with the computer and dims while the session is locked, as logind
tells it (gdbus monitor or dbus-monitor, one line at a time)."""

import pytest

from libre_panel import logind, presence


@pytest.fixture(autouse=True)
def nobody_away():
    presence.reset()
    yield
    presence.reset()


class Process:
    """A program the watcher started: whether it still runs."""

    def __init__(self, args, **_):
        self.args, self.ended = args, False
        self.stdout = iter(())  # the monitor's lines: none (the tests hand them over)

    def poll(self):
        return 0 if self.ended else None

    def terminate(self):
        self.ended = True


@pytest.fixture
def started(monkeypatch):
    """The programs the watcher starts, by the first word of each; all of them are there."""
    seen = []

    def popen(args, **kw):
        seen.append(Process(args, **kw))
        return seen[-1]

    monkeypatch.setattr(logind.subprocess, "Popen", popen)
    monkeypatch.setattr(logind.shutil, "which", lambda name: f"/usr/bin/{name}")
    return seen


def watcher(calls, session="/org/freedesktop/login1/session/_32"):
    return logind.LogindWatcher(
        on_sleep=lambda: calls.append("sleep"),
        on_wake=lambda: calls.append("wake"),
        session=session,
        command=["gdbus", "monitor"],
    )


def test_this_session_s_path_is_escaped_as_systemd_escapes_it():
    assert logind.session_path("2") == "/org/freedesktop/login1/session/_32"
    assert logind.session_path("c1") == "/org/freedesktop/login1/session/c1"
    assert logind.session_path("12") == "/org/freedesktop/login1/session/_312"
    assert logind.session_path("") is None


def test_the_panel_goes_dark_before_sleep_and_the_computer_may_sleep_then(started):
    calls = []
    watch = watcher(calls).start()
    inhibitor = [p for p in started if p.args[0] == "systemd-inhibit"]
    assert len(inhibitor) == 1 and "--mode=delay" in inhibitor[0].args
    watch.heard("/org/freedesktop/login1: org.freedesktop.login1.Manager.PrepareForSleep (true,)\n")
    assert calls == ["sleep"] and inhibitor[0].ended  # dark first, then let go
    watch.heard(
        "/org/freedesktop/login1: org.freedesktop.login1.Manager.PrepareForSleep (false,)\n"
    )
    assert calls == ["sleep", "wake"]
    again = [p for p in started if p.args[0] == "systemd-inhibit"]
    assert len(again) == 2 and not again[1].ended  # held again for the next sleep
    watch.stop()
    assert all(p.ended for p in started)


def test_locking_this_session_dims_the_panels_and_another_s_does_not(started):
    watch = watcher([]).start()
    ours, other = "/org/freedesktop/login1/session/_32", "/org/freedesktop/login1/session/_33"
    hint = ": org.freedesktop.DBus.Properties.PropertiesChanged ('org.freedesktop.login1.Session',"
    watch.heard(f"{other}{hint} {{'LockedHint': <true>}}, @as [])")
    assert not presence.away()
    watch.heard(f"{ours}{hint} {{'LockedHint': <true>}}, @as [])")
    assert presence.away()
    watch.heard(f"{ours}{hint} {{'LockedHint': <false>}}, @as [])")
    assert not presence.away()
    watch.heard(f"{ours}: org.freedesktop.login1.Session.Lock ()")
    assert presence.away()
    watch.heard(f"{ours}: org.freedesktop.login1.Session.Unlock ()")
    assert not presence.away()


def test_dbus_monitor_s_lines_are_understood_too(started):
    calls = []
    watch = watcher(calls).start()
    head = "signal time=1.5 sender=:1.4 -> destination=(null destination) serial=9 path="
    for line in (
        f"{head}/org/freedesktop/login1; interface=org.freedesktop.login1.Manager; "
        "member=PrepareForSleep",
        "   boolean true",
    ):
        watch.heard(line)
    assert calls == ["sleep"]
    for line in (
        f"{head}/org/freedesktop/login1/session/_32; interface=org.freedesktop.DBus.Properties; "
        "member=PropertiesChanged",
        '   string "org.freedesktop.login1.Session"',
        "   array [",
        "      dict entry(",
        '         string "Active"',
        "         variant             boolean true",  # (not the one asked about)
        "      )",
        "      dict entry(",
        '         string "LockedHint"',
        "         variant             boolean true",
        "      )",
    ):
        watch.heard(line)
    assert presence.away()
    watch.heard(f"{head}/org/freedesktop/login1/session/_32; "
                "interface=org.freedesktop.login1.Session; member=Unlock")  # fmt: skip
    assert not presence.away()


def test_without_a_monitor_nothing_is_heard_and_the_tray_goes_on(monkeypatch):
    from libre_panel import tray

    monkeypatch.setattr(logind.shutil, "which", lambda name: None)
    assert logind.monitor_command() is None
    with pytest.raises(OSError):
        logind.LogindWatcher(command=None).start()
    monkeypatch.setattr(tray.sys, "platform", "linux")
    assert tray._follow_logind(object()) is None  # (said in the log, and on it goes)
