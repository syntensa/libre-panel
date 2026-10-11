"""App clips for tools/readme_media.py: the app in a browser, with a cursor drawn on it.

The app (src/libre_panel/editor/app/, served by the editor's server) runs in headless
Chromium under Playwright, in a settings folder of its own (named LibrePanel, one theme in it,
English) and with the preview's clock and values standing still. A clip is a script of what a
person does: the mouse glides, keys are pressed, and a picture is taken after each step, once
the page has stopped changing (no request in flight and nothing changed on the page for a
moment). So a clip does not depend on how fast the machine is, and nothing in it is
animated by the clock: the app's own transitions are switched off (a hover or a menu changes
in one frame).

Playwright and Chromium are optional (``pip install playwright``, then ``playwright install
chromium``): without them an app clip is skipped, with a word about it.
"""

from __future__ import annotations

import contextlib
import io
import json
import math
import os
import shutil
import tempfile
import threading
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from unittest import mock

import readme_images as images
from media_encode import FrameSink, Recorded, Settings
from media_panel import LAYOUTS, modules_theme, theme_data
from PIL import Image

from libre_panel.config import user_themes_dir
from libre_panel.theme.model import parse_theme, save_theme
from libre_panel.theme.modules import make_grid

VIEWPORT = (1280, 720)  # the app in a window of a laptop: the library, the panel, the inspector
DENSITY = 2.0  # pixels of the picture to a pixel of the page: text is drawn at twice the size ...
FPS = 25  # ... and shrunk to the GIF's width, which is sharper than a page drawn small
QUIET_MS = 90  # the page has to be still this long before a picture: longer than the preview's
# throttle (45 ms), so that the picture the app asks for after a change is waited for
THEME = "my-panel"  # what the theme is called in the clip's home

# Runs in the page before the app does: a cursor that follows the mouse (Playwright's mouse
# has none) and a way to tell that the page is still: no request in flight, no change to the
# page for ``ms`` (what the cursor does itself is no change).
INIT_SCRIPT = """
(() => {
  let pending = 0;
  let last = performance.now();
  const touch = () => { last = performance.now(); };
  const realFetch = window.fetch;
  window.fetch = function (...args) {
    pending++;
    touch();
    return realFetch.apply(this, args).finally(() => { pending--; touch(); });
  };
  window.__idle = (ms) => pending === 0 && performance.now() - last >= ms;
  // A long timer (a message that goes away after four seconds) runs on the clip's own time, which
  // moves with its pictures: on the wall clock it would go at a different picture on a slower
  // machine. The short ones (the app's throttles and debounces) are left alone.
  const realSetTimeout = window.setTimeout.bind(window);
  const realClearTimeout = window.clearTimeout.bind(window);
  const slow = new Map();
  let clipTime = 0;
  let slowId = -1;
  window.setTimeout = (fn, ms, ...args) => {
    if (typeof fn !== "function" || !(ms >= 1500)) return realSetTimeout(fn, ms, ...args);
    slow.set(slowId, { at: clipTime + ms / 1000, fn, args });
    return slowId--;
  };
  window.clearTimeout = (id) => (slow.delete(id) ? undefined : realClearTimeout(id));
  window.__advance = (seconds) => {
    clipTime += seconds;
    for (const [id, timer] of [...slow]) {
      if (timer.at > clipTime) continue;
      slow.delete(id);
      try { timer.fn(...timer.args); } catch (error) { console.error(error); }
    }
  };
  const start = () => {
    const keys = document.createElement("div");
    keys.id = "__keys";
    document.documentElement.appendChild(keys);
    window.__keys = (label) => {
      keys.innerHTML = label ? label.split(" ").map((k) => `<b>${k}</b>`).join("") : "";
      keys.className = label ? "on" : "";
    };
    const cursor = document.createElement("div");
    cursor.id = "__cursor";
    cursor.innerHTML =
      '<svg width="28" height="28" viewBox="0 0 28 28"><path d="M4 3 L4 22 L9 17.4 L12.4 25 ' +
      'L15.6 23.6 L12.2 16 L19 15.6 Z" fill="#fff" stroke="#0a1119" stroke-width="1.7" ' +
      'stroke-linejoin="round"/></svg><i></i>';
    document.documentElement.appendChild(cursor);
    const style = document.createElement("style");
    style.textContent =
      "#__cursor{position:fixed;left:0;top:0;width:28px;height:28px;z-index:2147483647;" +
      "pointer-events:none;transform:translate(-200px,-200px)}" +
      "#__cursor svg{position:absolute;left:0;top:0;" +
      "filter:drop-shadow(0 1px 2px rgba(0,0,0,.55))}" +
      "#__cursor i{position:absolute;left:-9px;top:-9px;width:26px;height:26px;border-radius:50%;" +
      "border:2px solid #13e5d7;background:rgba(19,229,215,.22);opacity:0}" +
      "#__cursor.down i{opacity:1}" +
      "*,*::before,*::after{transition:none!important;animation:none!important;" +
      "caret-color:transparent!important;scroll-behavior:auto!important}" +
      "#__keys{position:fixed;left:50%;bottom:26px;transform:translateX(-50%);display:none;" +
      "gap:8px;z-index:2147483646;pointer-events:none}#__keys.on{display:flex}" +
      "#__keys b{font:600 15px/1 system-ui,sans-serif;color:#eaf2f8;" +
      "background:rgba(14,22,32,.95);border:1px solid #33465a;border-bottom-width:3px;" +
      "border-radius:8px;padding:9px 13px;box-shadow:0 6px 18px rgba(0,0,0,.45)}";
    document.head.appendChild(style);
    const move = (e) => {
      cursor.style.transform = `translate(${e.clientX - 4}px,${e.clientY - 3}px)`;
    };
    addEventListener("pointermove", move, true);
    addEventListener("mousemove", move, true);
    addEventListener("pointerdown", () => cursor.classList.add("down"), true);
    addEventListener("pointerup", () => cursor.classList.remove("down"), true);
    new MutationObserver((records) => {
      for (const r of records) {
        const node = r.target.nodeType === 1 ? r.target : r.target.parentElement;
        if (!node || !node.closest || !node.closest("#__cursor, #__keys")) { touch(); return; }
      }
    }).observe(document, { subtree: true, childList: true, attributes: true, characterData: true });
  };
  if (document.readyState === "loading") addEventListener("DOMContentLoaded", start);
  else start();
})();
"""


class Unavailable(RuntimeError):
    """The app cannot be recorded here (no Playwright, no Chromium)."""


def ease(x: float) -> float:
    """From 0 to 1 and gently: a hand, not a machine."""
    x = min(1.0, max(0.0, x))
    return x * x * x * (x * (6 * x - 15) + 10)


class Camera:
    """The browser of a clip: a cursor that glides, keys, and a picture after each step."""

    def __init__(
        self,
        page: Any,
        sink: FrameSink,
        settings: Settings,
        box: dict[str, int],
        pointer: tuple[float, float] | None = None,
    ):
        self.page, self.sink, self.quick = page, sink, settings.quick
        self.box = box  # the part of the window that is in the picture
        self.pointer = self.home = pointer or (VIEWPORT[0] * 0.62, VIEWPORT[1] * 0.8)
        self.poster = -1

    # -- pictures

    def settle(self, quiet: int = QUIET_MS) -> None:
        """Wait until the page is still (a slow answer is waited for, up to a few seconds)."""
        try:
            self.page.wait_for_function(f"window.__idle({quiet})", timeout=5000, polling=20)
        except Exception:  # a page that never settles is shot as it is
            pass

    def shot(self, copies: int = 1) -> None:
        """A picture of the window, for ``copies`` frames; the clip's time goes on by that."""
        png = self.page.screenshot(clip=self.box)
        self.sink.add(Image.open(io.BytesIO(png)), copies)
        self.page.evaluate("(seconds) => window.__advance(seconds)", copies / FPS)

    def frames(self, seconds: float) -> int:
        return max(1, round(seconds * FPS / (5 if self.quick else 1)))

    def hold(self, seconds: float) -> None:
        """Stand still for ``seconds``."""
        self.settle()
        self.shot(self.frames(seconds))

    def mark(self) -> None:
        """The next picture is the clip's poster."""
        self.poster = self.sink.count

    def toasts_away(self, limit: float = 4.5) -> None:
        """Hold still until the app's messages have gone (they go by the clip's own time), so
        that a clip ends as it began. In a quick clip the time just passes."""
        if self.quick:
            self.page.evaluate("(seconds) => window.__advance(seconds)", limit)
            self.settle()
            self.shot()
            return
        waited = 0.0
        while waited < limit and self.page.locator(".toasts .toast").count():
            self.hold(0.2)
            waited += 0.2

    # -- the page

    def open(self, route: str, ready: str) -> None:
        self.page.goto(f"{self.page.url.split('#')[0]}#/{route}")
        self.page.wait_for_selector(ready)
        self.settle(300)
        self.page.mouse.move(*self.pointer)  # the cursor is there from the start

    def point(self, locator: Any) -> tuple[float, float]:
        """The middle of an element, in the window."""
        locator.scroll_into_view_if_needed()
        box = locator.bounding_box()
        assert box is not None, "the element is not on the page"
        return box["x"] + box["width"] / 2, box["y"] + box["height"] / 2

    def panel_point(self, x: float, y: float) -> tuple[float, float]:
        """A place on the panel (its own pixels) in the window."""
        canvas = self.page.locator(".panel-canvas").bounding_box()
        width = self.page.evaluate("window.libre.store.get().render.size[0]")
        scale = canvas["width"] / width
        return canvas["x"] + x * scale, canvas["y"] + y * scale

    # -- the mouse

    def glide(self, x: float, y: float, seconds: float = 0.7, arc: float = 0.0) -> None:
        """Move the cursor there in ``seconds``, a picture for each step."""
        x0, y0 = self.pointer
        steps = self.frames(seconds)
        length = math.hypot(x - x0, y - y0) or 1.0
        for k in range(1, steps + 1):
            e = ease(k / steps)
            bend = arc * length * math.sin(math.pi * e)
            px = x0 + (x - x0) * e - (y - y0) / length * bend
            py = y0 + (y - y0) * e + (x - x0) / length * bend
            self.page.mouse.move(px, py)
            self.pointer = (px, py)
            self.settle()
            self.shot()

    def park(self, seconds: float = 0.7) -> None:
        """Back to where the mouse was at first, so that a clip ends as it began (and loops)."""
        if math.hypot(self.pointer[0] - self.home[0], self.pointer[1] - self.home[1]) > 2:
            self.glide(*self.home, seconds)

    def press(self) -> None:
        self.page.mouse.down()
        self.settle()
        self.shot(self.frames(0.12))

    def release(self) -> None:
        self.page.mouse.up()
        self.settle()
        self.shot()

    def click(self, x: float, y: float, seconds: float = 0.6) -> None:
        self.glide(x, y, seconds)
        self.press()
        self.release()

    def key(self, name: str, hold: float = 0.2, label: str = "") -> None:
        """Press a key (or keys: ``Control+k``); ``label`` (``Ctrl K``) shows them on the
        screen while they matter, as a screencast does."""
        if label:
            self.page.evaluate("(label) => window.__keys(label)", label)
        self.page.keyboard.press(name)
        self.settle()
        self.shot(self.frames(hold))
        if label:
            self.page.evaluate("() => window.__keys('')")

    def type(self, text: str, per_key: float = 0.12) -> None:
        for letter in text:
            self.page.keyboard.press(letter)
            self.settle(60)
            self.shot(self.frames(per_key))

    def scroll(self, dy: float, seconds: float = 0.8) -> None:
        steps = self.frames(seconds)
        done = 0.0
        for k in range(1, steps + 1):
            to = dy * ease(k / steps)
            self.page.mouse.wheel(0, to - done)
            done = to
            self.settle(60)
            self.shot()


# -- what the app runs on ---------------------------------------------------------------------


def room_for_two(data: dict[str, Any]) -> dict[str, Any]:
    """The theme with room on its grid for a module of two cells, in one row: its last module
    goes if there is none."""
    modules = [w for w in data["widgets"] if w["type"] == "module"]
    while modules:
        theme = parse_theme(data)
        grid = make_grid(theme.width, theme.height, theme.grid, theme.model)
        taken = {
            (c, r)
            for w in modules
            for c in range(w["col"], w["col"] + w["cols"])
            for r in range(w["row"], w["row"] + w["rows"])
        }
        if any(
            (c, r) not in taken and (c + 1, r) not in taken
            for r in range(grid.rows)
            for c in range(grid.columns - 1)
        ):
            break
        data["widgets"].remove(modules.pop())
    return data


@contextlib.contextmanager
def running(
    theme: dict[str, Any], folder: Path | None = None, language: str = "en"
) -> Iterator[str]:
    """The editor's server on a port of its own, with ``theme`` (and the files in ``folder``)
    in a home called LibrePanel; yields its address."""
    from libre_panel.config import set_config_value
    from libre_panel.editor import server as editor
    from libre_panel.plugins import loader
    from libre_panel.sensors.demo import demo_snapshot

    def preview(fixed_time: float | None = None, **kwargs: Any) -> Any:
        return demo_snapshot(fixed_time or images.WHEN, **kwargs)  # the clock stands still

    with tempfile.TemporaryDirectory(prefix="readme-media-app-") as parent:
        home = Path(parent) / "LibrePanel"
        home.mkdir()
        with contextlib.ExitStack() as stack:
            stack.enter_context(mock.patch.dict(os.environ, {"LIBRE_PANEL_HOME": str(home)}))
            stack.enter_context(mock.patch.object(loader, "_installed", lambda group: iter(())))
            stack.enter_context(mock.patch.object(editor, "demo_snapshot", preview))
            loader.reset_registry()
            if folder is not None:
                shutil.copytree(folder, user_themes_dir() / THEME, dirs_exist_ok=True)
            save_theme(THEME, theme)
            set_config_value("theme", THEME)
            set_config_value("language", language)
            server = editor.make_server(port=0)
            threading.Thread(target=server.serve_forever, daemon=True).start()
            try:
                yield f"http://127.0.0.1:{server.server_address[1]}/"
            finally:
                server.shutdown()
                server.server_close()
                server.editor_state.close()
                loader.reset_registry()


# -- clips ------------------------------------------------------------------------------------


@dataclass(frozen=True)
class Around:
    """The part of the window around an element, with this much room (in pixels of the page) on
    each side: a close-up that finds its own place whatever the layout of the app does."""

    selector: str
    left: int = 0
    top: int = 0
    right: int = 0
    bottom: int = 0


def window_box(page: Any, crop: tuple[int, int, int, int] | Around | None) -> dict[str, int]:
    """The part of the window a clip is shot of: all of it, a box, or the room around an
    element."""
    width, height = VIEWPORT
    if crop is None:
        crop = (0, 0, width, height)
    if isinstance(crop, Around):
        found = page.locator(crop.selector).first.bounding_box()
        assert found is not None, f"{crop.selector} is not on the page"
        x0 = max(0, math.floor(found["x"]) - crop.left)
        y0 = max(0, math.floor(found["y"]) - crop.top)
        x1 = min(width, math.ceil(found["x"] + found["width"]) + crop.right)
        y1 = min(height, math.ceil(found["y"] + found["height"]) + crop.bottom)
        crop = (x0, y0, x1 - x0, y1 - y0)
    x, y, w, h = crop
    return {"x": x, "y": y, "width": w, "height": h}


@dataclass(frozen=True)
class AppClip:
    """One clip of the app: the theme it opens with, where it looks, and what a person does."""

    about: str
    seconds: float  # about how long it is: the script decides
    script: Callable[[Camera], None]
    themes: tuple[str, ...] = ("layout-desk",)
    crop: tuple[int, int, int, int] | Around | None = None  # of the window: (x, y, width, height)
    cursor: tuple[float, float] | None = None  # where the mouse rests at first, in the window's
    # width and height (the default is the empty canvas)
    route: str = "design"  # where the app is opened
    ready: str = ".panel-canvas .preview"  # what is there when it is
    room: bool = False  # the theme is given room on its grid for a module of two cells

    @property
    def kind(self) -> str:
        return "app"


def small_panel(modules: list[dict[str, Any]]) -> dict[str, Any]:
    """A 3.5" panel (what Libre Panel starts with, a grid of three cells by two) made of
    ``modules``, in the Arctic look and with a name of its own."""
    data = modules_theme("arctic", modules, refresh_ms=500, model="turing-3.5")
    data["name"] = "My panel"
    return data


def layout_desk() -> dict[str, Any]:
    """A clock and a ring, and a row of room under them for what is dragged in."""
    return small_panel(
        [
            {"module": "clock", "col": 0, "row": 0, "cols": 2, "rows": 1},
            {"module": "ring", "col": 2, "row": 0, "cols": 1, "rows": 1, "source": "cpu"},
        ]
    )


def layout_rings() -> dict[str, Any]:
    """A large ring (it shows its history under it) and two small ones: the sizes at which the
    finish of a ring can be told apart."""
    return small_panel(
        [
            {"module": "ring", "col": 0, "row": 0, "cols": 2, "rows": 2, "source": "cpu"},
            {"module": "ring", "col": 2, "row": 0, "cols": 1, "rows": 1, "source": "gpu"},
            {"module": "ring", "col": 2, "row": 1, "cols": 1, "rows": 1, "source": "mem"},
        ]
    )


LAYOUTS["layout-desk"] = layout_desk
LAYOUTS["layout-rings"] = layout_rings


def hardware_names(page: Any) -> None:
    """What the app is told about the hardware's names: stand-ins, not this computer's own (they
    show in Settings, and beside a widget that shows a name)."""
    names = {
        "cpu": {"detected": "8-Core Processor", "custom": ""},
        "gpu": {"detected": "Graphics Card", "custom": ""},
        "memory": {"detected": "DDR5-5600 32 GB", "custom": ""},
    }

    def answer(route: Any, request: Any) -> None:
        if request.method == "POST":
            for part, text in json.loads(request.post_data or "{}").items():
                names[part]["custom"] = text
        route.fulfill(status=200, content_type="application/json", body=json.dumps(names))

    page.route("**/api/hardware", answer)


def record(
    name: str,
    clip: AppClip,
    settings: Settings,
    folder: Path,
    themes: tuple[str, ...] | None = None,
) -> Recorded:
    """Run ``clip`` in the app (opened with ``themes`` instead of its own) and keep its frames
    in ``folder``."""
    try:
        from playwright.sync_api import sync_playwright
    except ImportError as error:
        raise Unavailable(
            "Playwright is not installed (pip install playwright; playwright install chromium)"
        ) from error
    theme, source = theme_data((themes or clip.themes)[0])
    if clip.room:
        theme = room_for_two(theme)
    sink = FrameSink(folder, FPS)
    camera: Camera | None = None
    with running(theme, source) as url, sync_playwright() as playwright:
        try:
            browser = playwright.chromium.launch()
        except Exception as error:  # the browser is not installed
            raise Unavailable(f"Chromium is not available: {str(error).splitlines()[0]}") from error
        try:
            context = browser.new_context(
                viewport={"width": VIEWPORT[0], "height": VIEWPORT[1]},
                device_scale_factor=min(DENSITY, max(0.5, DENSITY * settings.scale)),
                locale="en-US",
                bypass_csp=True,  # for the cursor and the page's own stillness
            )
            context.add_init_script(INIT_SCRIPT)
            page = context.new_page()
            hardware_names(page)  # (the app asks what this computer is called)
            page.goto(url)
            rest = clip.cursor and (clip.cursor[0] * VIEWPORT[0], clip.cursor[1] * VIEWPORT[1])
            camera = Camera(page, sink, settings, window_box(page, None), rest)
            camera.open(clip.route, clip.ready)
            camera.box = window_box(page, clip.crop)
            clip.script(camera)
            camera.toasts_away()
            camera.park()
        finally:
            browser.close()
    frames = sink.frames()
    assert camera is not None
    return Recorded(frames, camera.poster if camera.poster >= 0 else frames.count // 2)


# -- what the clips show ----------------------------------------------------------------------

FREE_CELLS = """() => {
  const grid = window.libre.store.get().render.grid;
  const taken = new Set();
  for (const w of window.libre.doc.widgets()) {
    if (w.type !== "module") continue;
    for (let c = w.col; c < w.col + w.cols; c++)
      for (let r = w.row; r < w.row + w.rows; r++) taken.add(c + "," + r);
  }
  const free = [];
  for (let r = 0; r < grid.rows; r++)
    for (let c = 0; c < grid.columns; c++) if (!taken.has(c + "," + r)) free.push([c, r]);
  return { grid, free };
}"""
RING = """() => {
  const kinds = ["ring", "instrument", "stat", "graph"];
  for (const kind of kinds) {
    const found = window.libre.doc.widgets().find((w) => w.type === "module" && w.module === kind);
    if (found) return found.id;
  }
  const any = window.libre.doc.widgets().find((w) => w.type === "module");
  return any ? any.id : null;
}"""


def cell_centre(grid: dict[str, Any], col: int, row: int) -> tuple[float, float]:
    """The middle of a cell of the panel's grid, in the panel's own pixels."""
    return (
        grid["x"] + col * (grid["cell_w"] + grid["gap"]) + grid["cell_w"] / 2,
        grid["y"] + row * (grid["cell_h"] + grid["gap"]) + grid["cell_h"] / 2,
    )


def wait_for_library(camera: Camera) -> None:
    """The library draws a picture of every module, a moment after the app opens."""
    camera.page.wait_for_function("document.querySelectorAll('.tile .tile-picture img').length > 8")
    camera.settle(300)


def drag_module(camera: Camera) -> None:
    """A module from the library onto free cells of the panel, then wider (its size, in the
    toolbar over it), and the two undone again."""
    page = camera.page
    wait_for_library(camera)
    camera.hold(0.5)
    found = page.evaluate(FREE_CELLS)
    grid, free = found["grid"], {tuple(cell) for cell in found["free"]}
    col, row = next(
        (c, r) for c, r in sorted(free, key=lambda a: (a[1], a[0])) if (c + 1, r) in free
    )
    camera.glide(*camera.point(page.locator('.tile[data-item="module:weather"]')), 0.8, arc=0.12)
    camera.press()
    camera.glide(*camera.panel_point(*cell_centre(grid, col, row)), 1.0, arc=-0.1)
    camera.hold(0.5)  # the cells it takes are lit, with its name and size
    camera.release()
    camera.mark()
    camera.hold(0.7)
    camera.click(*camera.point(page.locator(".floating-toolbar .tool-text.mono")), 0.6)  # 2 x 1
    grow = (col + 2, row) in free  # one more cell to the right, else one less
    size = page.get_by_role("dialog", name="Size")  # (the inspector has the same buttons)
    step = size.get_by_role("button", name="Wider" if grow else "Narrower")
    camera.click(*camera.point(step), 0.45)
    camera.hold(1.0)
    camera.key("Escape", 0.25)
    for _ in range(2):  # (the size is undone, and the module)
        camera.key("Control+z", 0.4)
    camera.hold(0.3)


def ring_style(camera: Camera) -> None:
    """A ring in the three finishes it has: light, facet, flat."""
    page = camera.page
    ring = page.evaluate(RING)
    camera.hold(0.6)
    camera.click(*camera.point(page.locator(f'.overlay .box[data-id="{ring}"]')), 0.7)
    camera.hold(0.5)
    for n, style in enumerate(("Light", "Facet", "Flat")):
        button = page.locator(".floating-toolbar [role='radio']", has_text=style)
        camera.click(*camera.point(button), 0.55)
        if n == 0:
            camera.mark()
        camera.hold(1.0)
    camera.key("Escape", 0.3)


def palette(camera: Camera) -> None:
    """Ctrl+K: a few letters find a command, Enter runs it (a look, for the whole panel)."""
    page = camera.page
    accent = page.evaluate("window.libre.doc.theme().palette.accent || ''").upper()
    look = "sunset" if accent == "#FF4FD8" else "neon"  # (the one the theme does not have)
    camera.hold(0.9)
    camera.key("Control+k", 0.6, "Ctrl K")
    camera.type(look)
    camera.mark()
    camera.hold(0.9)
    camera.key("Enter", 0.5, "Enter")
    camera.hold(1.6)
    camera.key("Control+z", 0.6, "Ctrl Z")  # and back
    camera.hold(0.7)


def look(camera: Camera) -> None:
    """Look: a colour world for the whole screen, the notices in the band style, and a notice
    as the theme draws it; then the two changes undone."""
    page = camera.page
    camera.hold(0.5)
    camera.click(*camera.point(page.get_by_role("radio", name="Neon")), 0.6)
    camera.mark()
    camera.hold(0.9)
    band = page.get_by_role("radio", name="Band")
    camera.glide(1050.0, 400.0, 0.4)  # (the wheel turns what is under the mouse)
    down = band.bounding_box()["y"] - 400
    camera.scroll(down, 0.7)  # to the notices
    camera.click(*camera.point(band), 0.45)
    camera.hold(0.2)
    camera.click(*camera.point(page.get_by_role("button", name="Show a notice")), 0.6)
    camera.hold(0.2)
    camera.click(*camera.point(page.get_by_role("menuitem", name="Volume")), 0.35)
    camera.hold(1.3)
    for _ in range(2):  # (the notice goes with the first, the colours with the second)
        camera.key("Control+z", 0.4, "Ctrl Z")
    camera.scroll(-down, 0.6)  # and up again


def settings_names(camera: Camera) -> None:
    """Settings, Sensors, Hardware names: a name of your own for the processor, and back."""
    page = camera.page
    camera.hold(0.6)
    camera.pointer = (760.0, 500.0)  # (the wheel turns what is under the mouse)
    camera.glide(*camera.pointer, 0.5)
    field = page.locator("#hardware-cpu-row")
    camera.scroll(field.bounding_box()["y"] - 320, 1.1)  # down to the card
    camera.hold(0.4)
    camera.click(*camera.point(field), 0.6)
    camera.type("Workstation", 0.09)
    camera.mark()
    camera.key("Enter", 0.2)
    camera.hold(1.0)
    camera.click(
        *camera.point(page.locator(".settings-row", has_text="Processor").locator(".link-btn")), 0.6
    )
    camera.hold(0.6)


CLIPS: dict[str, AppClip] = {
    "app-look": AppClip(
        "the app: Look, a colour world for the whole screen, notices as a band, and one shown",
        8.5, look, themes=("lumen",), route="look", ready=".look-stage img",
        cursor=(0.4, 0.82),
    ),
    "app-drag-module": AppClip(
        "the app: a module from the library onto the panel, and wider by its size",
        7.0, drag_module, room=True,
    ),
    "app-ring-style": AppClip(
        "the app: a ring as light, as facets, and flat",
        7.0, ring_style, themes=("layout-rings",), crop=Around(".panel-canvas", 46, 50, 46, 52),
    ),
    "app-command-palette": AppClip(
        "the app: Ctrl+K, a few letters, Enter: a look for the whole panel",
        6.5, palette, cursor=(0.5, 0.86),
    ),
    "app-hardware-names": AppClip(
        "the app: Settings, Sensors, a name of your own for the processor",
        7.0, settings_names, route="settings/sensors", ready="#hardware-cpu-row",
        cursor=(0.6, 0.78),
    ),
}  # fmt: skip
