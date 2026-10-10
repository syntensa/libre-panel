"""Toasts and transitions in the real main loop, frame by frame on a fake clock.

The scenarios the SPUR II mod was checked with against its original (refresh
in place, rank, takeover, toasts during the tube and the entrance, a screen
that shows a kind itself, several toasts at once, a game that ends during its
own entrance) run through ``app.run`` with an in-memory video panel. They
assert behaviour: which toast shows, how old it is, and whether it is drawn.
"""

import json
import math
import time

import pytest

from libre_panel import app
from libre_panel.config import Config, ModeConfig, SensorsConfig
from libre_panel.devices.base import Display
from libre_panel.plugins import PluginHost
from libre_panel.plugins.host import ServiceHost
from libre_panel.render import overlays
from libre_panel.render.band import BandStyle
from libre_panel.render.renderer import Renderer

STEP = 1 / 64  # a frame; exact in binary, so frame times add up exactly
LIFE = 0.45 + 2.0 + 0.32  # a band's roll in, the theme's hold time and its roll out


def frames(seconds):
    return math.floor(seconds / STEP)


class Log:
    """What every frame showed: its time, the theme, the toast screens saw
    (text, age) and the toast the style drew (text, age, the one before)."""

    def __init__(self):
        self.frames = []

    def rendered(self, renderer, now):
        seen = renderer.toast
        self.frames.append(
            {
                "now": now,
                "theme": renderer.theme.name,
                "seen": (seen[0].text, seen[1]) if seen else None,
                "drawn": None,
            }
        )

    def drew(self, toast, age, previous):
        if self.frames:
            self.frames[-1]["drawn"] = (toast.text, age, previous and previous[0].text)

    def seen(self, n):
        return self.frames[n]["seen"]

    def drawn(self, n):
        return self.frames[n]["drawn"]


class Script:
    """Stands in for the main loop's pacer: frame n is drawn at ``base + n *
    STEP``; what services do for frame n happens just before it."""

    def __init__(self, length, events):
        self.length, self.events, self.n = length, events, 0
        self.base = float(math.ceil(time.perf_counter()) + 1)  # after frame 0, and exact

    def reset(self):
        pass

    def wait(self, fps, stop):
        self.n += 1
        if self.n >= self.length:
            stop.set()
        for action in self.events.get(self.n, ()):
            action()
        return self.base + self.n * STEP


class Panel(Display):
    """An in-memory video panel: every frame goes out."""

    streaming = True
    stream_fps = 50

    def describe(self):
        return "memory"

    def show(self, frame, region=None):
        pass


def write_theme(home, name, color, **toast):
    data = {
        "format": "libre-panel-theme/1",
        "name": name,
        "display": {"model": "custom", "width": 480, "height": 320},
        "background": {"color": color},
        "palette": {"accent": "#13E5D7", "bg": "#0A1119"},
        "widgets": [],
        "toast": {"style": "test.band", "queue": False, "seconds": 2.0, "anchor": "bottom"},
    }
    data["toast"].update(toast)
    folder = home / "themes" / name
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "theme.json").write_text(json.dumps(data), encoding="utf-8")


@pytest.fixture
def scenario(monkeypatch, isolated_home):
    """Runs ``app.run`` for ``length`` frames with ``events`` ({frame: [action]}),
    and returns the log of every frame."""
    log = Log()

    class Recording(BandStyle):
        name = "test.band"

        def draw(self, frame, toast, age, previous=None):
            log.drew(toast, round(age, 6), previous)
            return super().draw(frame, toast, age, previous)

    render = Renderer.render

    def rendered(self, snapshot, now=None):
        log.rendered(self, now)
        return render(self, snapshot, now)

    monkeypatch.setitem(overlays.BUILT_IN_STYLES, "test.band", Recording)
    monkeypatch.setattr(Renderer, "render", rendered)
    monkeypatch.setattr(app, "create_display", Panel)
    made = []
    make = app.transition

    def transition(spec, context=None):
        made.append(spec)
        return make(spec, context)

    monkeypatch.setattr(app, "transition", transition)
    write_theme(isolated_home, "classic", "#102030")
    write_theme(isolated_home, "studio", "#203040", off=["music", "volume"])  # shows them itself
    write_theme(isolated_home, "focus", "#302010")
    write_theme(isolated_home, "designed", "#102030", seconds=3.0,
                options={"roll_in": 0.6, "roll_out": 0.5})  # fmt: skip
    host = PluginHost()

    def run(length, events, theme="classic"):
        script = Script(length, events)
        monkeypatch.setattr(app, "_Pacer", lambda: script)
        config = Config(
            theme=theme,
            fps=50,
            sensors=SensorsConfig(providers=["demo"]),
            modes={"game": ModeConfig(theme="focus")},
            transition="tube",
        )
        try:
            app.run(config, host=host)
        finally:
            host.close()
        assert len(log.frames) == length
        return log

    run.host = host
    run.made = made
    return run


def notify(host, text, kind, rank=0, **payload):
    service = ServiceHost(host, kind)
    return lambda: service.notify(text, kind=kind, rank=rank, payload=payload or None)


def age(n, since):
    return pytest.approx((n - since) * STEP)


def life_of(log, start, text):
    """The frames from ``start`` on in which ``text`` shows to screens."""
    return [n for n in range(start, len(log.frames)) if (log.seen(n) or ("",))[0] == text]


# -- (a) the same key: in place, rolling in, standing and leaving ---------------------


def test_a_volume_turned_refreshes_one_band(scenario):
    host = scenario.host
    turn = {
        10: [notify(host, "30", "volume", progress=0.30)],
        15: [notify(host, "32", "volume", progress=0.32)],  # rolling in
        60: [notify(host, "34", "volume", progress=0.34)],  # standing
        194: [notify(host, "36", "volume", progress=0.36)],  # leaving: 60 + 2.0 s + a bit
        400: [notify(host, "38", "volume", progress=0.38)],  # long gone: afresh
    }
    log = scenario(600, turn)
    assert log.seen(9) is None and log.drawn(9) is None
    assert log.drawn(10) == ("30", 0.0, None) and log.seen(10) == ("30", 0.0)
    assert log.drawn(15) == ("32", 0.0, "30") and log.seen(16) == ("32", age(16, 15))
    assert log.drawn(60) == ("34", 0.0, "32") and log.drawn(194) == ("36", 0.0, "34")
    end = life_of(log, 194, "36")[-1] + 1
    assert all(log.drawn(n) for n in range(10, end))  # never a frame without the band
    # "36" goes on from part way up: it stays less than a band coming afresh, but
    # longer than its hold and roll out
    assert frames(2.32) < end - 194 < frames(LIFE)
    assert all(log.seen(n) is None and log.drawn(n) is None for n in range(end, 400))
    assert log.drawn(400) == ("38", 0.0, None)  # afresh
    assert life_of(log, 400, "38") == list(range(400, 400 + frames(LIFE) + 1))


def test_the_hold_and_roll_times_come_from_the_theme(scenario):
    log = scenario(400, {10: [notify(scenario.host, "30", "volume")]}, theme="designed")
    assert life_of(log, 0, "30") == list(range(10, 10 + frames(0.6 + 3.0 + 0.5) + 1))
    assert all(log.drawn(n) for n in range(10, 10 + frames(4.1)))


# -- (b) rank: a higher rank holds, lower ones are dropped ----------------------------


def test_a_warning_holds_back_lower_ranks(scenario):
    host = scenario.host
    log = scenario(
        330,
        {
            10: [notify(host, "CPU 95 °C", "warning", rank=2)],
            40: [notify(host, "USB drive", "device", rank=1)],
            50: [notify(host, "30", "volume")],
            250: [notify(host, "USB drive again", "device", rank=1)],
        },
    )
    assert life_of(log, 0, "CPU 95 °C") == list(range(10, 10 + frames(LIFE) + 1))
    shown = {log.seen(n)[0] for n in range(330) if log.seen(n)}
    assert shown == {"CPU 95 °C", "USB drive again"}  # the lower ones never showed
    assert log.drawn(250) == ("USB drive again", 0.0, None)


# -- (c) the same rank with another key takes over ------------------------------------


def test_the_same_rank_takes_over(scenario):
    host = scenario.host
    log = scenario(
        420,
        {
            10: [notify(host, "Song", "music")],
            15: [notify(host, "Mail", "mail")],  # while rolling in
            80: [notify(host, "Call", "phone")],  # while standing
            80 + frames(2.1): [notify(host, "Timer", "timer")],  # while leaving
        },
    )
    assert log.drawn(15) == ("Mail", 0.0, "Song")
    # it rolls on with the band that began at frame 10
    assert life_of(log, 15, "Mail") == list(range(15, 80))
    assert log.drawn(80) == ("Call", 0.0, "Mail")
    timer = 80 + frames(2.1)
    assert log.drawn(timer) == ("Timer", 0.0, "Call")
    end = life_of(log, timer, "Timer")[-1] + 1
    assert frames(2.32) < end - timer < frames(LIFE)  # it rolled back up from part way
    assert all(log.drawn(n) for n in range(10, end))


# -- (d) and (f): the tube lets toasts go on over it -----------------------------------


def test_a_toast_goes_on_over_the_tube(scenario):
    host = scenario.host
    log = scenario(
        200,
        {
            10: [notify(host, "30", "volume")],
            40: [lambda: host.request_theme("focus", by="autopilot", transition="tube")],
            60: [notify(host, "Mail", "mail")],  # (f) during the tube: at once
        },
    )
    assert log.frames[40]["theme"] == "focus" and ("tube" in scenario.made)
    assert all(log.drawn(n) == ("30", age(n, 10), None) for n in range(10, 60))  # (d) on its clock
    assert log.drawn(60) == ("Mail", 0.0, "30") and log.seen(60) == ("Mail", 0.0)
    assert all(log.drawn(n) for n in range(60, 60 + frames(1.0)))


# -- (e) the entrance hides toasts; they are decided all the same ------------------------


def test_toasts_during_the_entrance(scenario):
    host = scenario.host
    entrance = ("entrance", {"title": "Space Game", "kind": "start"})
    log = scenario(
        440,
        {
            10: [notify(host, "30", "volume")],
            40: [lambda: host.set_mode("game", by="game", transition=entrance)],
            80: [notify(host, "USB drive", "device", rank=1)],
            120: [notify(host, "Mail", "mail", rank=2)],
            130: [notify(host, "Song", "music", rank=0)],  # lower: dropped
        },
    )
    end = 40 + math.ceil(3.2 / STEP)  # the frame the entrance is over
    assert log.frames[40]["theme"] == "focus" and scenario.made[-1] == entrance
    assert all(log.drawn(n) is None for n in range(40, end))  # nothing over the entrance
    # screens see them, one after the other, starting again on every frame
    assert [log.seen(n) for n in (79, 80, 81, 119, 120, 121, 200)] == [
        ("30", STEP),
        ("USB drive", 0.0),
        ("USB drive", STEP),
        ("USB drive", STEP),
        ("Mail", 0.0),
        ("Mail", STEP),
        ("Mail", STEP),
    ]
    # the frame the entrance ends already shows the last one, rolling in afresh
    assert log.drawn(end) == ("Mail", STEP, None)
    assert all(log.drawn(n)[:1] == ("Mail",) for n in range(end, end + frames(2.0)))
    assert life_of(log, end, "Mail")[-1] == end + frames(LIFE - STEP)


# -- the game ends during its own entrance ---------------------------------------------


def test_a_game_that_ends_during_its_entrance(scenario):
    host = scenario.host
    start = ("entrance", {"title": "Space Game", "kind": "start"})
    over = ("entrance", {"title": "Space Game", "kind": "end"})
    log = scenario(
        560,
        {
            20: [lambda: host.set_mode("game", by="game", transition=start)],
            60: [notify(host, "CPU 95 °C", "warning", rank=2)],
            84: [lambda: host.set_mode(None, by="game", transition=over)],  # 1 s after it began
        },
    )
    assert log.frames[20]["theme"] == "focus" and log.frames[84]["theme"] == "classic"
    assert scenario.made[-2:] == [start, over]  # the end's own entrance, not lost
    end = 84 + math.ceil(3.2 / STEP)  # it starts again from the frame on show
    assert all(log.drawn(n) is None for n in range(20, end))
    assert log.seen(end - 1) == ("CPU 95 °C", STEP)
    assert log.drawn(end) == ("CPU 95 °C", STEP, None)  # afresh, once it is over


def test_a_report_during_the_entrance_is_revealed_by_it(scenario):
    host = scenario.host
    over = ("entrance", {"title": "Space Game", "kind": "end"})
    host.set_mode("game", by="game")
    log = scenario(
        400,
        {
            20: [lambda: host.set_mode(None, by="game", transition=over)],
            84: [lambda: host.request_theme("studio", by="report", priority=10)],
        },
        theme="classic",
    )
    assert log.frames[84]["theme"] == "studio"  # shown at once, under the running entrance
    assert scenario.made.count(over) == 1 and "tube" not in scenario.made[1:]  # no second one


def test_a_mode_change_brings_its_own_transition_into_a_following_one(scenario, monkeypatch):
    """A following transition uncovers a theme asked for meanwhile, but a mode
    change with a transition of its own starts that one from the frame on show."""
    from PIL import Image

    from libre_panel.plugins import Transition

    class Reveal(Transition):  # follows, and asks for nothing else
        name = "test.reveal"
        duration = 3.0
        switch = "follow"

        def frame(self, old, new, t):
            return Image.new("RGB", new.size, "magenta")

    monkeypatch.setitem(overlays.BUILT_IN_TRANSITIONS, "test.reveal", Reveal)
    host = scenario.host
    log = scenario(
        300,
        {
            10: [lambda: host.set_mode("game", by="game", transition="test.reveal")],
            30: [notify(host, "Mail", "mail")],  # waits while the reveal plays
            60: [lambda: host.set_mode(None, by="game", transition="tube")],  # theme and mode
            150: [lambda: host.set_mode("game", by="game", transition="test.reveal")],
            200: [lambda: host.set_mode("quiet", by="game", transition="tube")],  # mode alone
        },
    )
    assert scenario.made == ["test.reveal", "tube", "test.reveal", "tube"]
    assert log.frames[60]["theme"] == "classic" and log.frames[200]["theme"] == "classic"
    assert all(log.drawn(n) is None for n in range(30, 60))
    assert log.drawn(60) == ("Mail", 0.0, None)  # the tube lets it go on: it starts at once


# -- (g, g2) a screen that shows a kind itself --------------------------------------------


def test_a_toast_that_comes_on_a_screen_that_shows_its_kind_is_dropped(scenario):
    host = scenario.host
    log = scenario(
        200,
        {
            10: [notify(host, "Song", "music")],
            40: [lambda: host.request_theme("classic", by="autopilot", transition="tube")],
        },
        theme="studio",
    )
    assert log.frames[40]["theme"] == "classic"
    assert all(log.seen(n) is None and log.drawn(n) is None for n in range(200))


def test_a_toast_on_show_goes_on_unseen_and_comes_back(scenario):
    host = scenario.host
    log = scenario(
        260,
        {
            10: [notify(host, "30", "volume")],
            40: [lambda: host.request_theme("studio", by="autopilot", transition="tube")],
            70: [notify(host, "quiet", "quiet", rank=-1)],  # held back by the unseen one
            104: [lambda: host.request_theme(None, by="autopilot", transition="tube")],
        },
    )
    assert log.frames[40]["theme"] == "studio" and log.frames[104]["theme"] == "classic"
    assert all(log.seen(n) is None and log.drawn(n) is None for n in range(40, 104))
    # back on its own clock, as if it had stayed
    assert all(log.drawn(n) == ("30", age(n, 10), None) for n in range(104, 10 + frames(LIFE)))
    assert life_of(log, 0, "30") == [*range(10, 40), *range(104, 10 + frames(LIFE) + 1)]
    assert not any(log.seen(n) and log.seen(n)[0] == "quiet" for n in range(260))


# -- (h) two toasts in the same frame -----------------------------------------------------


def test_two_toasts_in_the_same_frame(scenario):
    host = scenario.host
    log = scenario(
        240,
        {10: [notify(host, "30", "volume"), notify(host, "CPU 95 °C", "warning", rank=2)]},
    )
    assert log.seen(10) == ("CPU 95 °C", 0.0)
    assert log.drawn(10) == ("CPU 95 °C", 0.0, "30")  # it took over the one before at once
    assert not any(log.drawn(n) and log.drawn(n)[0] == "30" for n in range(240))
    assert life_of(log, 0, "CPU 95 °C") == list(range(10, 10 + frames(LIFE) + 1))
