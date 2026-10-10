"""Game mode: finding the game (a list of programs, Steam's library, the window in
front, the linger), and the switch with its entrance, through the host and the
service manager, with a made-up computer and a Steam folder in a temp dir."""

import threading

import pytest
from fake_sensors import wait_for
from fake_system import FakeMachine, game_path, make_logo, make_steam

from libre_panel import i18n
from libre_panel.config import Config, ServicesConfig
from libre_panel.plugins import PluginHost, ServiceManager
from libre_panel.sensors.base import Snapshot
from libre_panel.services import game as game_service
from libre_panel.services import machine as computer
from libre_panel.services.game import Detector, Found, parse_programs
from libre_panel.services.steam import Library


class Clock:
    """Time by hand; it stays far below the hour a test's worker would wait."""

    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        return self.now


# -- finding a game -------------------------------------------------------------------------------


@pytest.fixture
def steam(tmp_path):
    root = make_steam(
        tmp_path / "Steam",
        ("10", "Space Game", "SpaceGame"),
        ("20", "Other Game", "Other"),
        ("30", "Wallpaper Engine", "wallpaper_engine"),
        ("40", "Proton 9.0", "Proton 9.0"),
    )
    make_logo(root, 10, sub="season1")
    return root


def detector(root, machine, programs=(), **options):
    given = parse_programs(list(programs), lambda text: None)
    return Detector(machine, Library(str(root)), given, **options)


def test_programs_are_listed_with_a_name_to_show():
    problems = []
    found = parse_programs(
        ["Game.EXE", " eldenring.exe = Elden Ring ", "retroarch", "", "= nothing", 5],
        problems.append,
    )
    assert found == {"game.exe": "game", "eldenring.exe": "Elden Ring", "retroarch": "retroarch"}
    assert len(problems) == 3 and "5" in problems[2]  # the empty ones, and the number


def test_a_program_in_the_list_is_a_game_whatever_its_name(steam):
    machine = FakeMachine()
    machine.start("Notepad.exe", 1)
    look = detector(steam, machine, ["MyGame.exe = My Game", "other.exe"], steam=False).look
    assert look(0) is None
    machine.start("mygame.EXE", 7, "/games/mygame.exe")
    assert look(1) == Found("mygame.exe", "My Game", 7, "/games/mygame.exe", "")
    machine.start("other.exe", 8)
    assert look(2).name == "My Game"  # the first of the list that runs


def test_a_listed_game_from_steam_has_its_number_for_the_logo(steam):
    machine = FakeMachine()
    machine.start("space.exe", 7, game_path(steam, "SpaceGame", "space.exe"))
    found = detector(steam, machine, ["space.exe"], steam=False).look(0)
    assert (found.name, found.appid) == ("space", "10")


def test_a_steam_game_counts_once_its_window_has_been_in_front(steam):
    machine = FakeMachine()
    look = detector(steam, machine).look
    machine.start("steam.exe", 1, str(steam / "steam.exe"))
    machine.start("game.exe", 7, game_path(steam, "SpaceGame"))
    assert look(0) is None  # it runs, but nobody has seen its window
    machine.front = 7
    found = look(2)
    assert (found.name, found.pid, found.appid, found.key) == ("Space Game", 7, "10", "game.exe")
    machine.front = 1  # alt-tab: it is still the game
    assert look(4).name == "Space Game"


def test_where_the_window_in_front_is_not_known_the_program_running_is_enough(steam):
    machine = FakeMachine(has_foreground=False)
    machine.start("game.exe", 7, game_path(steam, "SpaceGame"))
    assert detector(steam, machine).look(0).name == "Space Game"


def test_tools_and_programs_outside_the_library_are_no_game(steam):
    machine = FakeMachine(has_foreground=False)
    machine.start("wallpaper32.exe", 2, game_path(steam, "wallpaper_engine", "wallpaper32.exe"))
    machine.start("wine64", 3, game_path(steam, "Proton 9.0", "files/bin/wine64"))
    machine.start("game.exe", 4, "/somewhere/else/game.exe")
    machine.start("nameless", 5, "")
    assert detector(steam, machine).look(0) is None


def test_steam_can_be_left_out(steam):
    machine = FakeMachine(has_foreground=False)
    machine.start("game.exe", 7, game_path(steam, "SpaceGame"))
    assert detector(steam, machine, steam=False).look(0) is None
    assert machine.looked == 0  # not even the programs are asked for


def test_the_mode_stays_a_few_seconds_after_the_game_ended(steam):
    machine = FakeMachine(has_foreground=False)
    pid = machine.start("game.exe", 7, game_path(steam, "SpaceGame"))
    look = detector(steam, machine, linger_s=6.0).look
    assert look(100).name == "Space Game"
    machine.end(pid)
    assert look(103).name == "Space Game" and look(106).name == "Space Game"
    assert look(106.5) is None  # more than 6 s


def test_a_game_that_comes_back_within_the_linger_is_the_same(steam):
    machine = FakeMachine(has_foreground=False)
    pid = machine.start("game.exe", 7, game_path(steam, "SpaceGame"))
    look = detector(steam, machine, linger_s=6.0).look
    first = look(0)
    machine.end(pid)
    look(3)
    machine.start("game.exe", 8, game_path(steam, "SpaceGame"))
    again = look(5)
    assert again.key == first.key and again.pid == 8
    assert look(20).pid == 8  # and the linger starts from the last time it was seen


def test_the_game_in_front_stays_the_game_while_another_one_runs(steam):
    machine = FakeMachine(has_foreground=False)
    machine.start("other.exe", 5, game_path(steam, "Other", "other.exe"))
    look = detector(steam, machine).look
    assert look(0).name == "Other Game"
    machine.start("game.exe", 2, game_path(steam, "SpaceGame"))
    machine.running.reverse()  # the one that comes now is first in the list
    assert look(1).name == "Other Game"  # and does not swap it


def test_a_program_that_was_in_front_before_must_come_to_the_front_again(steam):
    machine = FakeMachine()
    pid = machine.start("game.exe", 7, game_path(steam, "SpaceGame"))
    machine.front = 7
    look = detector(steam, machine, linger_s=1.0).look
    assert look(0) is not None
    machine.end(pid)
    assert look(5) is None  # forgotten with its process
    machine.start("game.exe", 8, game_path(steam, "SpaceGame"))
    assert look(6) is None  # a new process is not the game until its window was in front
    machine.front = 8
    assert look(7).pid == 8


# -- the service ----------------------------------------------------------------------------------


class Desk:
    """A host with the game service running, a clock by hand, a made-up computer
    and a Steam folder."""

    def __init__(self, root, monkeypatch, **options):
        self.machine = FakeMachine()
        monkeypatch.setattr(computer, "current", lambda: self.machine)
        self.clock = Clock()
        self.host = PluginHost()
        self.manager = ServiceManager(self.host)  # the registry: Libre Panel's own services
        options = {"steam_path": str(root), "poll_s": 3600, **options}
        self.manager.apply(Config(services=ServicesConfig(["game"], {"game": options})))
        assert self.manager.state == {"game": "running"}, self.manager.state
        self.service = self.manager.running["game"][0]
        self.service.clock = self.service.library.clock = self.clock
        self.root = root

    def play(self, installdir="SpaceGame", pid=100, exe="game.exe", front=True):
        self.machine.start(exe, pid, game_path(self.root, installdir, exe))
        if front:
            self.machine.front = pid
        return pid

    def look(self, seconds=0.0):
        self.clock.now += seconds
        self.service.detect()

    def sample(self, logo=True):
        """The main loop's next sample; with the logo if the service published one."""
        images = self.host.images() if logo else {}
        self.host.emit("sample", wait=True, snapshot=Snapshot(images=images))

    def mode(self):
        return self.host.mode

    def close(self):
        self.manager.stop()
        self.host.close()


@pytest.fixture
def desk(steam, monkeypatch):
    made = []

    def make(**options):
        made.append(Desk(steam, monkeypatch, **options))
        return made[-1]

    yield make
    for each in made:
        each.close()


def test_a_game_starts_with_its_name_and_logo_before_the_mode_begins(desk):
    d = desk()
    d.look()
    assert d.mode() is None and d.host.published().get("game.name") is None  # nothing runs
    d.play()
    d.look(2)
    assert d.host.published()["game.name"].value == "Space Game"  # for the first game screen,
    assert d.host.images()["game.logo"].size == (440, 120)  # cut out and not scaled
    assert d.mode() is None  # and the mode waits for a sample that has the logo
    d.sample(logo=False)
    assert d.mode() is None
    d.sample()
    assert d.mode() == "game"
    kind, params = d.host.take_transition()
    assert kind == "entrance"
    assert (params["kind"], params["title"]) == ("start", "Space Game")
    assert params["logo"].size == (
        440,
        120,
    )  # the picture itself, not a key that waits for a sample
    assert d.service.in_mode and d.host.take_toasts() == []  # the entrance says it


def test_a_sample_that_does_not_come_does_not_hold_the_switch_for_ever(desk):
    d = desk()
    d.play()
    d.look()
    d.service.advance()
    assert d.mode() is None
    d.clock.now += game_service.SAMPLE_WAIT_S - 0.1
    d.service.advance()
    assert d.mode() is None
    d.clock.now += 0.2
    d.service.advance()
    assert d.mode() == "game"


def test_a_game_without_a_logo_switches_at_once(desk):
    d = desk()
    d.play("Other", exe="other.exe")  # no logo in Steam's cache
    d.look()
    assert d.mode() == "game" and d.host.images() == {}
    kind, params = d.host.take_transition()
    assert "logo" not in params and params["title"] == "Other Game"


def test_the_logo_can_be_left_out(desk):
    d = desk(logo=False)
    d.play()
    d.look()
    assert d.mode() == "game" and d.host.images() == {}


def test_a_listed_program_is_a_game_too(desk):
    d = desk(programs=["emulator.exe = Retro Games"], steam=False)
    d.machine.start("emulator.exe", 9, "/opt/emulator.exe")
    d.look()
    assert d.mode() == "game" and d.host.published()["game.name"].value == "Retro Games"
    assert d.host.take_transition()[1]["title"] == "Retro Games"


def test_the_game_ends_a_few_seconds_after_its_program(desk):
    d = desk()
    pid = d.play()
    d.look()
    d.sample()
    d.host.take_transition()
    d.machine.end(pid)
    d.look(3)
    assert d.mode() == "game"  # still the linger
    d.look(4)  # 7 s after it was last seen
    assert d.mode() is None and not d.service.in_mode
    kind, params = d.host.take_transition()
    assert (kind, params["kind"], params["title"]) == ("entrance", "end", "Space Game")
    assert params["logo"].size == (440, 120)  # the end shows it too
    assert d.host.published()["game.name"].value is None and d.host.images() == {}


def test_without_the_entrance_a_message_says_so(desk):
    d = desk(entrance=False)
    pid = d.play()
    d.look()
    d.sample()
    assert d.mode() == "game" and d.host.take_transition() is None  # the theme's own
    [toast] = d.host.take_toasts()
    assert (toast.text, toast.kind, toast.key, toast.rank, toast.icon) == (
        "Space Game", "game", "game", 1, "gamepad",
    )  # fmt: skip
    assert toast.payload == {"label": "Game mode"} and toast.seconds == 3.0
    d.machine.end(pid)
    d.look(10)
    [bye] = d.host.take_toasts()
    assert (bye.text, bye.payload) == ("Space Game", {"label": "Game ended"})
    assert d.mode() is None and d.host.take_transition() is None


def test_the_texts_follow_the_language(desk):
    i18n.set_language("de")
    d = desk(entrance=False, force=True)
    d.look()
    [toast] = d.host.take_toasts()
    assert (toast.text, toast.payload["label"]) == ("Spielmodus", "Spielmodus")


def test_a_game_gone_before_the_mode_began_leaves_nothing(desk):
    d = desk(linger_s=0.0)
    pid = d.play()
    d.look()
    d.machine.end(pid)
    d.look(1)
    d.sample()
    assert d.mode() is None and d.host.take_transition() is None
    assert d.host.published()["game.name"].value is None and d.host.images() == {}
    assert d.service.waiting is None
    d.service.advance()
    assert d.mode() is None


def test_the_next_game_changes_the_name_and_logo_not_the_mode(desk):
    d = desk(linger_s=0.0)
    first = d.play(pid=1)
    d.look()
    d.sample()
    d.host.take_transition()
    switches = d.host.switches
    d.play("Other", pid=2, exe="other.exe")
    d.machine.end(first)
    d.look(1)
    assert d.mode() == "game" and d.host.switches == switches  # no second entrance
    assert d.host.published()["game.name"].value == "Other Game"
    assert d.host.images() == {}  # that one has no logo; the old one is gone


def test_the_switches_decide_what_is_looked_for(desk):
    d = desk(automatic=False)
    d.play()
    d.look()
    d.sample()
    assert d.mode() is None and d.machine.looked == 0  # it does not even look
    d.close()

    d = desk(force=True)
    d.look()
    assert d.mode() == "game" and d.host.published()["game.name"].value == "Game mode"
    kind, params = d.host.take_transition()
    assert params == {"kind": "start"}  # no game to name, no logo
    d.look(100)
    assert d.mode() == "game"  # it stays
    d.play()
    d.look()
    d.sample()
    assert d.host.published()["game.name"].value == "Space Game"  # a game that runs is named


def test_the_mode_ends_when_the_service_stops(desk):
    d = desk(force=True)
    d.look()
    assert d.mode() == "game"
    d.manager.apply(Config())
    assert d.mode() is None and d.host.published().get("game.name") is None
    assert d.host.images() == {}


# -- the worker -----------------------------------------------------------------------------------


def test_the_worker_looks_by_itself_and_keeps_going_when_a_look_fails(steam, monkeypatch, caplog):
    machine = FakeMachine(has_foreground=False)
    monkeypatch.setattr(computer, "current", lambda: machine)
    host = PluginHost()
    manager = ServiceManager(host)
    options = {"steam_path": str(steam), "poll_s": 0.25, "linger_s": 0.0}
    config = Config(services=ServicesConfig(["game"], {"game": options}))
    failing = [True]
    programs = machine.programs

    def looking():
        if failing[0]:
            raise OSError("cannot list the programs")
        return programs()

    machine.programs = looking
    try:
        with caplog.at_level("INFO", logger="libre_panel.service.game"):
            manager.apply(config)
            assert wait_for(lambda: "cannot list the programs" in caplog.text, timeout=10)
            failing[0] = False
            assert wait_for(lambda: "works again" in caplog.text, timeout=10)
        assert caplog.text.count("looking for games failed") == 1  # once, not every round
        machine.start("game.exe", 7, game_path(steam, "Other", "game.exe"))
        assert wait_for(lambda: host.mode == "game", timeout=10)  # no logo: no sample needed
        machine.end(7)
        assert wait_for(lambda: host.mode is None, timeout=10)
    finally:
        manager.stop()
        host.close()


def test_the_worker_waits_for_a_sample_that_has_the_logo(steam, monkeypatch):
    machine = FakeMachine(has_foreground=False)
    monkeypatch.setattr(computer, "current", lambda: machine)
    host = PluginHost()
    manager = ServiceManager(host)
    options = {"steam_path": str(steam), "poll_s": 0.25}
    manager.apply(Config(services=ServicesConfig(["game"], {"game": options})))
    stop = threading.Event()

    def main_loop():  # what the loop does: a snapshot with the published images, now and then
        while not stop.wait(0.02):
            host.emit("sample", snapshot=Snapshot(images=host.images()))

    sampler = threading.Thread(target=main_loop)
    sampler.start()
    try:
        machine.start("game.exe", 7, game_path(steam, "SpaceGame"))
        assert wait_for(lambda: host.mode == "game", timeout=10)
        assert host.take_transition()[1]["title"] == "Space Game"
    finally:
        stop.set()
        sampler.join(5)
        manager.stop()
        host.close()


# -- the real main loop ---------------------------------------------------------------------------


ENTRANCE = 215  # frames of a 3.2 s entrance (a frame is 1/64 s on the scenarios' clock)


def test_a_game_starts_and_ends_in_the_main_loop(steam, monkeypatch, isolated_home, caplog):
    """A game runs: the loop's own samples carry the logo to the service, which
    switches the mode; the entrance plays, the game's theme shows. The game ends:
    the entrance for the end plays and the theme is the user's again."""
    from test_toast_scenarios import Panel, Script, write_theme

    from libre_panel import app
    from libre_panel.config import ModeConfig, SensorsConfig
    from libre_panel.render.renderer import Renderer

    for name, color in (("classic", "#102030"), ("focus", "#302010")):
        write_theme(isolated_home, name, color, style="card")
    themes, made = [], []  # the theme of every frame, and the transitions the loop made
    render, make = Renderer.render, app.transition

    def rendered(self, snapshot, now=None):
        themes.append(self.theme.name)
        return render(self, snapshot, now)

    def transition(spec, context=None):
        made.append(spec)
        return make(spec, context)

    machine = FakeMachine()
    monkeypatch.setattr(computer, "current", lambda: machine)
    monkeypatch.setattr(Renderer, "render", rendered)
    monkeypatch.setattr(app, "transition", transition)
    monkeypatch.setattr(app, "create_display", Panel)
    host = PluginHost()
    manager = ServiceManager(host)
    options = {"steam_path": str(steam), "poll_s": 3600, "linger_s": 0.0}
    config = Config(
        theme="classic",
        fps=50,
        refresh_ms=100,
        sensors=SensorsConfig(providers=["demo"]),
        services=ServicesConfig(["game"], {"game": options}),
        modes={"game": ModeConfig(theme="focus")},
        transition="tube",
    )
    manager.apply(config)
    service = manager.running["game"][0]
    seen = {}

    def game_starts():
        machine.start("game.exe", 100, game_path(steam, "SpaceGame"))
        machine.front = 100
        service.detect()

    def watch():  # the loop decides when the mode begins (its next sample): wait for it
        if "began" not in seen and host.mode == "game":
            seen["began"] = script.n
            seen["name"] = host.latest.value("game.name")  # for the first game screen
            seen["logo"] = host.latest.images.get("game.logo")
        elif "began" in seen and "ended" not in seen and script.n >= seen["began"] + ENTRANCE:
            seen["ended"] = script.n  # the entrance is over: the game ends
            machine.end(100)
            service.detect()

    events = {20: [game_starts], **{n: [watch] for n in range(21, 2000)}}
    script = Script(1000, events)
    monkeypatch.setattr(app, "_Pacer", lambda: script)
    try:
        with caplog.at_level("ERROR"):
            app.run(config, host=host)
    finally:
        manager.stop()
        host.close()

    began, ended = seen["began"], seen["ended"]
    assert len(themes) == 1000 and set(themes[:21]) == {"classic"}
    assert seen["name"] == "Space Game"
    assert seen["logo"] is not None and seen["logo"].size == (440, 120)  # in the loop's snapshots
    start, end = [spec for spec in made if isinstance(spec, tuple)]
    assert (start[0], start[1]["kind"], start[1]["title"]) == ("entrance", "start", "Space Game")
    assert (end[0], end[1]["kind"], end[1]["title"]) == ("entrance", "end", "Space Game")
    assert start[1]["logo"].size == end[1]["logo"].size == (440, 120)
    assert themes.index("focus") in (began, began + 1)  # the mode's theme from its first frame
    assert set(themes[began + 1 : ended]) == {"focus"}
    assert set(themes[ended + 1 :]) == {"classic"} and host.mode is None  # and the user's again
    assert "failed" not in caplog.text  # no entrance that broke
