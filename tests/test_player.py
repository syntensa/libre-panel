"""The app's Player (player.py): the files of its folder played on panels instead of their
themes, one after the other; paused, sought, moved on; in a panel's own process too; drawn at
the panel's size, and by the main loop over the theme."""

import io
import shutil
import subprocess
import threading
import time
from types import SimpleNamespace

import pytest
from PIL import Image, ImageDraw

from libre_panel import app, player
from libre_panel.config import Config, SensorsConfig
from libre_panel.devices.base import Display

FFMPEG = shutil.which("ffmpeg")
needs_ffmpeg = pytest.mark.skipif(FFMPEG is None, reason="ffmpeg is not installed")


@pytest.fixture(autouse=True)
def nothing_plays():
    player.stop()
    player.adopt(None)
    yield
    player.stop()
    player.adopt(None)


@pytest.fixture
def at(monkeypatch):
    """The player's clock, set by the test: ``at(5)`` is five seconds after the start."""
    moment = [1000.0]

    def set_to(seconds):
        moment[0] = 1000.0 + seconds

    monkeypatch.setattr(player, "clock", lambda: moment[0])
    return set_to


def picture(path, colour="red", size=(64, 48)):
    Image.new("RGB", size, colour).save(path)
    return path


def animation(path, durations=(100, 200, 300)):
    frames = [Image.new("RGB", (32, 32), c) for c in ("red", "green", "blue")[: len(durations)]]
    frames[0].save(path, save_all=True, append_images=frames[1:], duration=list(durations),
                   loop=0)  # fmt: skip
    return path


def clip(path, colours=("red",), seconds=1, size="64x48", rate=10):
    """A video of a colour a part, each ``seconds`` long."""
    parts = []
    for n, colour in enumerate(colours):
        part = path.with_name(f"{path.stem}-{n}.mp4")
        subprocess.run([FFMPEG, "-v", "error", "-y", "-f", "lavfi", "-i",
                        f"color=c={colour}:size={size}:rate={rate}", "-t", str(seconds),
                        "-pix_fmt", "yuv420p", str(part)], check=True, timeout=60)  # fmt: skip
        parts.append(part)
    listing = path.with_suffix(".txt")
    listing.write_text("".join(f"file '{p.name}'\n" for p in parts), encoding="utf-8")
    subprocess.run([FFMPEG, "-v", "error", "-y", "-f", "concat", "-safe", "0", "-i",
                    str(listing), "-c", "copy", str(path)], check=True, timeout=60)  # fmt: skip
    for part in parts:
        part.unlink()
    listing.unlink()
    return path


def test_a_file_s_name_is_one_of_the_library():
    assert player.valid_name("Holiday 2026.mp4") and player.valid_name("logo.GIF")
    assert player.valid_name("Ünïcode ✓.webm") and player.valid_name("still.jpeg")
    for name in ("../up.mp4", "a/b.mp4", "a\\b.mp4", ".hidden.mp4", "notes.txt", "", " x.mp4",
                 "x.mp4 ", "a:b.mp4", "x" * 200 + ".mp4", None, 5):  # fmt: skip
        assert not player.valid_name(name), name


@needs_ffmpeg
def test_the_library_is_the_folder_s_videos_and_pictures(isolated_home):
    where = player.folder()
    where.mkdir(parents=True)
    clip(where / "b clip.mp4", seconds=2)
    animation(where / "A moving.gif")
    picture(where / "c still.png")
    (where / "notes.txt").write_text("not media", encoding="utf-8")
    (where / "broken.mp4").write_bytes(b"no video in here")
    picture(where / ".hidden.png")
    (where / "folder.png").mkdir()
    found = player.library()
    assert [f.name for f in found] == ["A moving.gif", "b clip.mp4", "c still.png"]
    gif, video, still = found
    assert gif.kind == "animation" and gif.length == pytest.approx(0.6) and gif.size == (32, 32)
    assert video.kind == "video" and video.length == pytest.approx(2.0, abs=0.15)
    assert video.size == (64, 48) and video.fps == pytest.approx(10)
    assert still.kind == "picture" and still.length == 0 and still.bytes > 0
    assert player.library(isolated_home / "nowhere") == []


def test_a_playback_goes_from_one_to_the_next_as_its_loop_says(isolated_home, at):
    """A picture for the picture time, an animated picture in whole rounds for that long at
    least; after the last the first again ("all"), the same again ("one"), or the themes
    again ("off")."""
    where = player.folder()
    where.mkdir(parents=True)
    picture(where / "a.png")
    animation(where / "b.gif")  # (0.6 s a round: 9 rounds for 5 s, 5.4 s)
    picture(where / "c.png", "blue")
    names = ["a.png", "b.gif", "c.png", "missing.png", "../x.png"]
    at(0)
    told = player.play(names, panels=[1], picture_s=5)
    assert [i["name"] for i in told["items"]] == ["a.png", "b.gif", "c.png"]
    assert [i["seconds"] for i in told["items"]] == pytest.approx([5, 5.4, 5])

    def showing(seconds, number=1):
        at(seconds)
        found = player.now(number)
        return None if found is None else (found.path.name, round(found.position, 2))

    assert showing(0) == ("a.png", 0) and showing(4.9) == ("a.png", 4.9)
    assert showing(5) == ("b.gif", 0) and showing(10.3) == ("b.gif", 5.3)
    assert showing(10.4) == ("c.png", 0)
    assert showing(16.4) == ("a.png", 1)  # (round again: each from where the last ended)
    assert showing(16.4, number=2) is None  # (another panel: its theme)
    assert showing(60) is not None  # (long away: on from where the time says)
    player.configure(loop="one")
    status = player.status()
    one, began = status["name"], showing(60)[1]
    assert showing(60 + 6 - began) == (one, pytest.approx(1, abs=0.01))
    player.configure(loop="off")
    player.next_item()  # (round from the last to the first)
    at(100)
    player.play(names, panels=[1], start="c.png", loop="off", picture_s=5)
    assert showing(104) == ("c.png", 4) and showing(105) is None  # (the last, then the theme)
    assert player.status() is None


def test_a_playback_is_paused_sought_and_moved_on(isolated_home, at):
    where = player.folder()
    where.mkdir(parents=True)
    for name, colour in (("a.png", "red"), ("b.png", "green"), ("c.png", "blue")):
        picture(where / name, colour)
    at(0)
    player.play(["a.png", "b.png", "c.png"], panels=[2, 1, 2], picture_s=10)
    assert player.status()["panels"] == [1, 2]
    at(2)
    assert player.pause()["paused"] is True
    at(50)
    found = player.now(2)
    assert found.paused and found.path.name == "a.png" and found.position == 2
    jump = found.jump
    at(51)
    assert player.resume()["paused"] is False
    at(53)
    assert player.now(1).position == pytest.approx(4) and player.now(1).jump == jump
    status = player.seek(8.5)
    assert status["position"] == pytest.approx(8.5) and player.now(1).jump > jump
    assert player.seek(99)["position"] == pytest.approx(9.95)  # (within it)
    assert player.previous_item()["name"] == "a.png"  # (some seconds in: from its start)
    assert player.status()["position"] == 0
    assert player.previous_item()["name"] == "c.png"  # (at its start: the one before)
    assert player.next_item()["name"] == "a.png"
    player.pause()
    status = player.next_item()
    assert status["name"] == "b.png" and status["paused"] and status["position"] == 0
    assert player.toggle()["paused"] is False and player.toggle()["paused"] is True
    status = player.configure(fit="contain", picture_s=3, shuffle=True)
    assert status["fit"] == "contain" and status["shuffle"] and status["seconds"] == 3
    with pytest.raises(player.PlayerError):
        player.configure(loop="twice")
    with pytest.raises(player.PlayerError):
        player.play(["nothing.png"])
    with pytest.raises(player.PlayerError):
        player.play(["a.png"], panels=[0])
    with pytest.raises(player.PlayerError):
        player.play(["a.png"], fit="tile")
    player.stop()
    assert player.status() is None and player.now(1) is None
    assert player.pause() is None  # (nothing plays: nothing to pause)


def test_shuffled_the_one_chosen_plays_first(isolated_home, at):
    where = player.folder()
    where.mkdir(parents=True)
    names = [f"{n:02}.png" for n in range(12)]
    for name in names:
        picture(where / name)
    at(0)
    told = player.play(names, start="07.png", shuffle=True)
    order = [i["name"] for i in told["items"]]
    assert order[0] == "07.png" and sorted(order) == names and order != names


def test_a_panel_in_a_process_of_its_own_plays_along(isolated_home, at):
    """The first panel's process says what plays (export); a panel's own process plays the
    same (adopt), its position by the wall clock both read."""
    where = player.folder()
    where.mkdir(parents=True)
    picture(where / "a.png")
    at(0)
    player.play(["a.png"], panels=[2], picture_s=30)
    told = player.export()
    assert told == player.export()  # (the same until something changes: sent once)
    at(3)
    here = player.now(2)
    player.stop()
    player.adopt(told)  # (in the panel's process: nothing of its own plays there)
    there = player.now(2)
    assert there == here and player.now(1) is None
    player.adopt({"items": "broken"})
    assert player.now(2) is None


@needs_ffmpeg
def test_the_screen_draws_each_item_at_the_panel_s_size(tmp_path):
    """A video's frame of the moment, from where it was sought; a picture whole on black or
    across the panel; a new picture each time (the loop draws on it)."""
    video = clip(tmp_path / "red then blue.mp4", ("red", "blue"), seconds=1, size="64x48")
    still = picture(tmp_path / "wide.png", "green", size=(80, 20))
    screen = player.Screen()
    size = (64, 48)

    def drawn(path, kind, position, jump=1, fit="cover", until=lambda middle: middle != (0, 0, 0)):
        showing = player.Showing(path, kind, fit, position, jump, False, 10.0)
        deadline = time.monotonic() + 10
        while True:
            frame = screen.frame(showing, size, 30)
            middle = frame.getpixel((32, 24))
            if until(middle) or time.monotonic() > deadline:
                return frame, middle
            time.sleep(0.02)

    def blue(middle):
        return middle[2] > 200 and middle[0] < 60

    try:
        frame, middle = drawn(video, "video", 0.2)
        assert frame.size == size and frame.mode == "RGB"
        assert middle[0] > 200 and middle[2] < 60, middle  # red
        _frame, middle = drawn(video, "video", 1.5, jump=2, until=blue)  # (sought: from there)
        assert blue(middle), middle
        frame, middle = drawn(still, "picture", 0, fit="contain")
        assert middle[1] > 100 and frame.getpixel((32, 2)) == (0, 0, 0)  # (whole: room above)
        ImageDraw.Draw(frame).rectangle((0, 0, 63, 47), fill="white")  # (as a notice would)
        frame, middle = drawn(still, "picture", 0, fit="contain")
        assert frame.getpixel((32, 2)) == (0, 0, 0)  # (a new one: nothing of that)
        frame, _middle = drawn(still, "picture", 0, fit="cover")
        assert frame.getpixel((32, 2))[1] > 100  # (to the edges)
    finally:
        screen.close()


class Recording(Display):
    """A panel that is sent every frame, and keeps the last."""

    streaming = True
    stream_fps = 20

    def __init__(self, config):
        super().__init__(config)
        self.last = None

    def show(self, frame, region=None):
        self.last = frame.copy()


def test_the_main_loop_plays_instead_of_the_theme_and_then_shows_it_again(
    isolated_home, monkeypatch
):
    where = player.folder()
    where.mkdir(parents=True)
    picture(where / "magenta.png", (255, 0, 255), size=(32, 24))
    displays = []
    monkeypatch.setattr(app, "create_display", lambda config: displays.append(
        Recording(config)) or displays[-1])  # fmt: skip
    config = Config(theme="pico", sensors=SensorsConfig(providers=["demo"]))
    stop = threading.Event()
    thread = threading.Thread(target=app.run, args=(config,), kwargs={"stop": stop})
    thread.start()

    def waits_for(check):
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline:
            if displays and displays[0].last is not None and check(displays[0].last):
                return True
            time.sleep(0.05)
        return False

    def magenta(frame):
        return frame.getpixel((frame.width // 2, frame.height // 2))[:3] == (255, 0, 255)

    try:
        assert waits_for(lambda frame: not magenta(frame))  # the theme
        player.play(["magenta.png"], panels=[1], fit="cover")
        assert waits_for(magenta)  # the player's, across the panel
        player.stop()
        assert waits_for(lambda frame: not magenta(frame))  # the theme again
    finally:
        stop.set()
        thread.join(15)


# -- the app's routes -----------------------------------------------------------------------


@pytest.fixture
def served(isolated_home):
    """The app's server, with a background app (``True``) or as a plain editor."""
    from test_editor_server import request

    from libre_panel.editor.server import make_server

    servers = []

    def start(app=True):
        controls = SimpleNamespace(panel=SimpleNamespace(status=None)) if app else None
        server = make_server(port=0, controls=controls)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        servers.append(server)
        port = server.server_address[1]
        return lambda method, path, body=None, **k: request(port, method, path, body, **k)

    yield start
    for server in servers:
        server.shutdown()
        server.server_close()
        server.editor_state.close()


def test_the_app_adds_a_file_plays_it_and_takes_it_out(served):
    ask = served()
    png = io.BytesIO()
    Image.new("RGB", (40, 30), "red").save(png, format="PNG")
    status, data = ask("POST", "/api/player/media?name=Red%20one.png", png.getvalue(),
                       headers={"Content-Type": "application/octet-stream"})  # fmt: skip
    assert status == 200, data
    assert [f["name"] for f in data["library"]] == ["Red one.png"]
    assert (player.folder() / "Red one.png").is_file()
    assert not list(player.folder().glob(".*"))  # (written whole, then put in place)
    assert data["available"] and data["panels"][0]["number"] == 1 and data["playing"] is None
    status, picture = ask("GET", "/api/player/picture?name=Red%20one.png&w=20")
    assert status == 200 and Image.open(io.BytesIO(picture)).size == (20, 15)
    status, data = ask("POST", "/api/player", {"action": "play", "names": ["Red one.png"],
                                               "panels": [1], "fit": "contain"})  # fmt: skip
    assert status == 200 and data["playing"]["name"] == "Red one.png"
    assert data["playing"]["fit"] == "contain" and player.now(1) is not None
    for action in ("pause", "toggle", "next", "previous"):
        status, data = ask("POST", "/api/player", {"action": action})
        assert status == 200 and data["playing"] is not None, action
    status, data = ask("POST", "/api/player", {"action": "seek", "seconds": 4})
    assert data["playing"]["position"] == pytest.approx(4, abs=0.2)
    status, data = ask("POST", "/api/player", {"action": "set", "loop": "one", "picture_s": 5})
    assert data["playing"]["loop"] == "one" and data["playing"]["seconds"] == 5
    status, data = ask("POST", "/api/player", {"action": "stop"})
    assert status == 200 and data["playing"] is None and player.now(1) is None
    status, data = ask("POST", "/api/player", {"action": "remove", "name": "Red one.png"})
    assert status == 200 and data["library"] == []


def test_the_app_refuses_what_it_cannot_play(served):
    ask = served()
    for name in ("..%2Fup.png", "notes.txt", ".hidden.png", ""):
        status, data = ask("POST", f"/api/player/media?name={name}", b"x")
        assert status == 400, name
    status, data = ask("POST", "/api/player/media?name=fake.png", b"not a picture")
    assert status == 400 and not (player.folder() / "fake.png").exists()
    for wrong in ({"action": "dance"}, {"action": "play", "names": "a.png"},
                  {"action": "play", "names": ["a.png"], "panels": [True]},
                  {"action": "play", "names": ["missing.png"]},
                  {"action": "seek", "seconds": "4"}, {"action": "set", "fit": "tile"},
                  {"action": "remove", "name": "../config.toml"}, ["play"]):  # fmt: skip
        status, data = ask("POST", "/api/player", wrong)
        assert status == 400, wrong
    status, _data = ask("GET", "/api/player/picture?name=missing.png")
    assert status == 404
    # a plain editor (no running app) shows the library but plays nothing
    plain = served(app=False)
    status, data = plain("GET", "/api/player")
    assert status == 200 and data["available"] is False
    picture(player.folder() / "a.png")
    status, data = plain("POST", "/api/player", {"action": "play", "names": ["a.png"]})
    assert status == 409 and player.now(1) is None


def test_the_player_in_the_app_adds_plays_pauses_and_stops(isolated_home, tmp_path):
    """Player: a file added with Add files…, its tile plays it on the panel (a PNG file here)
    instead of the theme, Pause holds it, Stop shows the theme again."""
    from test_app_browser import browser_page, settings_app
    from test_service import wait_for

    app = settings_app(isolated_home)
    url = app.start()
    frame = isolated_home / "frame.png"
    chosen = picture(tmp_path / "Magenta (1).png", (255, 0, 255), size=(160, 90))

    def magenta():
        try:
            with Image.open(frame) as shown:
                return shown.convert("RGB").getpixel((shown.width // 2, shown.height // 2)) == (
                    255, 0, 255)  # fmt: skip
        except (OSError, ValueError):
            return False

    try:
        assert wait_for(lambda: app.panel.state()["state"] == "showing")
        with browser_page(url, "player") as page:
            page.wait_for_selector(".player-empty")
            page.locator(".player-library input[type=file]").set_input_files(str(chosen))
            tile = page.locator(".player-tile", has_text="Magenta (1).png")
            tile.wait_for()
            assert "Picture" in tile.inner_text()
            assert wait_for(lambda: not magenta())  # (the theme)
            tile.locator(".player-tile-play").click()
            page.get_by_role("button", name="Pause").wait_for()
            assert wait_for(magenta)  # the panel plays it
            assert "Magenta (1).png" in page.locator(".player-title").inner_text()
            page.get_by_role("button", name="Pause").click()
            page.get_by_role("button", name="Play").wait_for()
            assert player.status()["paused"] is True
            page.get_by_role("button", name="Stop: the theme again").click()
            assert wait_for(lambda: not magenta())  # the theme again
            assert player.status() is None
    finally:
        app.quit()
        app.shutdown()
