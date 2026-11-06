"""The app's pictures in a process of their own, while the app is open (editor/drawings.py has
them): every theme's picture, Design's and Look's picture of the theme, the building blocks'
and the modules', the looks of the weather symbols and the fans, the start screen's.

They are drawn with the renderer in layers of many sizes, and Python gives the memory it drew
them with back to the system only in part: its own blocks and the C heap stay split up wherever
a piece of anything is left in them. The background app's process (Linux, both panels on Lumen,
all services) held 75 MB before the app's pages had been open and 122 MB after, to the end
(on Windows 112 MB committed became 180). So they are drawn in a process that starts with the
first picture asked for and ends once the app has been closed for a minute (light mode:
:meth:`Pictures.stop`), and all of it goes with it: 93 MB after the same pages (88 with no
pictures drawn at all). Its drawing does not take the interpreter from the panels' either.

A job goes across by its name in drawings.py with what it needs (picklable), and comes back as
what it gives or as the error it raised (the same kind, with its text: :class:`PictureError`).
They are drawn one at a time there (:func:`child`). Where the process does not get going (it
ends before it says it is ready, :data:`START_TRIES` times), the jobs are done here, as before;
one that ends while it draws, once it was going, is not drawn here (what ended it would end the
panels' process too): that picture fails, and the next starts a new process. ``in_process`` (a
plain ``libre-panel editor``, the tests) does every job here."""

from __future__ import annotations

import logging
import multiprocessing
import os
import threading
from typing import Any

log = logging.getLogger(__name__)

_PIDS: set[int] = set()  # the pictures' processes running (Status → Diagnose → Load names them)


def picture_pids() -> frozenset[int]:
    """The ids of the pictures' processes that run."""
    return frozenset(_PIDS)


# the jobs for lists: each of their pictures (or of a list's parts) waits for a turn (here; in
# the pictures' process they are drawn one at a time anyway)
TURNS = frozenset({"module_previews", "preset_previews"})
FIRST = frozenset({"render"})  # drawn before the others waiting there: the picture being edited
LONGEST_S = 120.0  # a picture that takes longer than this is not waited for
START_TRIES = 2  # processes that end before they are ready: then the pictures are drawn here
STOP_S = 5.0  # how long the process has to end by itself before it is ended


def preview_slot_count() -> int:
    """How many pictures for lists and libraries (a theme's, a building block's) are drawn at
    once: two, fewer on a PC with fewer cores. A page asks for a screenful of them together, and
    drawing them all at once would take the processor from the request that matters, the theme
    that was just clicked to open."""
    try:
        cores = len(os.sched_getaffinity(0))
    except (AttributeError, OSError):  # (Windows and macOS have no such call)
        cores = os.cpu_count() or 1
    return max(1, min(2, cores - 1))


class PictureError(Exception):
    """A job raised ``kind`` (its class's name) with ``text`` in the pictures' process."""

    def __init__(self, kind: str, text: str) -> None:
        super().__init__(text)
        self.kind = kind
        self.text = text

    def __str__(self) -> str:
        return self.text


class _Gone(Exception):
    """The process ended before it answered."""


class _Wait:
    def __init__(self) -> None:
        self.came = threading.Event()
        self.answer: tuple[bool, Any] | None = None


def _run(job: str, args: tuple[Any, ...], turn: Any) -> Any:
    from libre_panel.editor import drawings

    if job in TURNS:
        return getattr(drawings, job)(*args, turn=turn)
    return getattr(drawings, job)(*args)


class Pictures:
    """Draws the app's pictures: in their own process (made on the first :meth:`ask`, ended by
    :meth:`stop`), or here with ``in_process``. ``turns()``: what a picture for a list waits for
    here (the server's preview slots)."""

    def __init__(self, in_process: bool = False, turns: Any = None) -> None:
        self.in_process = in_process
        self.turns = turns
        self._lock = threading.Lock()  # (the process: made, ended)
        self._send_lock = threading.Lock()
        self._process: Any = None
        self._conn: Any = None
        self._waits: dict[int, _Wait] = {}
        self._next = 0
        self._failed = False  # it did not start: the jobs are done here from then on
        self._misses = 0  # processes in a row that ended before they were ready
        self._ready: Any = None  # the connection of the last process that said it is ready
        self._broken: Any = None  # the connection of the last process that ended (its pipe)
        self._language = ""
        self._warming = False

    # -- asking --

    def warm(self) -> None:
        """Start the process meanwhile, where it does not run (a page of the app asks something:
        its pictures come next), so the first of them does not wait for it."""
        if self.in_process or self._failed or self._warming:
            return
        process = self._process
        if process is not None and process.is_alive():
            return
        self._warming = True

        def start() -> None:
            try:
                self._started()
            finally:
                self._warming = False

        threading.Thread(target=start, name="pictures-start", daemon=True).start()

    def ask(self, job: str, *args: Any) -> Any:
        """``job`` of drawings.py with ``args``, in the pictures' process; PictureError with the
        kind and the text of what it raised."""
        if not self.in_process and not self._failed:
            try:
                return self._ask_apart(job, args)
            except _Gone:
                log.info("the app's pictures: their process ended while drawing; drawn here")
        try:
            return _run(job, args, self.turns() if self.turns is not None else None)
        except Exception as exc:  # (the same as from the process: the caller knows one kind)
            raise PictureError(type(exc).__name__, str(exc)) from exc

    def _ask_apart(self, job: str, args: tuple[Any, ...]) -> Any:
        from libre_panel import i18n

        conn = self._started()
        if conn is None:
            raise _Gone
        wait = _Wait()
        with self._send_lock:
            self._next += 1
            ident = self._next
            self._waits[ident] = wait
            try:
                language = i18n.language()
                if language != self._language:  # (the pictures' words in the app's language)
                    conn.send(("language", language))
                    self._language = language
                conn.send(("job", ident, job, args))
            except (OSError, ValueError, EOFError) as exc:  # (it ended: the next starts anew)
                self._waits.pop(ident, None)
                self._broken = conn
                raise _Gone from exc
        if not wait.came.wait(LONGEST_S):
            with self._send_lock:
                self._waits.pop(ident, None)
            raise PictureError("TimeoutError", f"the picture took longer than {LONGEST_S:.0f} s")
        assert wait.answer is not None
        done, value = wait.answer
        if done is None:  # the process ended before it answered this
            if value:  # (it was going: what it drew may be what ended it)
                raise PictureError("ProcessEnded", "the pictures' process ended while drawing")
            raise _Gone
        if not done:
            raise PictureError(*value)
        return value

    # -- the process --

    def _started(self) -> Any:
        with self._lock:
            going = self._process is not None and self._broken is not self._conn
            if going and self._process.is_alive():
                return self._conn
            if self._process is not None:  # it ended by itself (or never got going)
                self._misses = 0 if self._ready is self._conn else self._misses + 1
                if self._misses >= START_TRIES:
                    log.warning("the app's pictures: their process ends before it answers; "
                                "drawn here")  # fmt: skip
                    self._failed = True
                    self._ended()
                    return None
            self._ended()
            try:
                ctx = multiprocessing.get_context("spawn")
                ours, theirs = ctx.Pipe()
                process = ctx.Process(target=child, args=(theirs,), name="pictures", daemon=True)
                process.start()
                theirs.close()
            except Exception:  # noqa: BLE001 - drawn here, as before
                log.warning("the app's pictures: no process of their own; drawn here",
                            exc_info=True)  # fmt: skip
                self._failed = True
                return None
            self._process, self._conn, self._language = process, ours, ""
            if process.pid is not None:
                _PIDS.add(process.pid)
            threading.Thread(target=self._hear, args=(ours,), name="pictures-hear",
                             daemon=True).start()  # fmt: skip
            log.info("the app's pictures: in a process of their own (%s)", process.pid)
            return ours

    def _hear(self, conn: Any) -> None:
        while True:
            try:
                message = conn.recv()
            except (EOFError, OSError):
                break
            if message[0] == "ready":
                self._ready = conn
            elif message[0] == "done":
                _kind, ident, done, value = message
                with self._send_lock:
                    wait = self._waits.pop(ident, None)
                if wait is not None:
                    wait.answer = (done, value)
                    wait.came.set()
            elif message[0] == "log":
                _kind, level, name, text = message
                logging.getLogger(name).log(level, "%s", text)
        self._gone(conn)

    def _gone(self, conn: Any) -> None:
        """The process (of ``conn``) ended: what waits for it is drawn here."""
        with self._send_lock:
            if conn is not self._conn and self._conn is not None:
                return  # (an older process's: a newer one answers its own)
            self._broken = conn
            waits, self._waits = self._waits, {}
        going = self._ready is conn
        for wait in waits.values():
            wait.answer = (None, going)
            wait.came.set()

    def _ended(self) -> None:
        """Lets go of a process that ended (or ends it): under ``_lock``."""
        process, conn = self._process, self._conn
        self._process = self._conn = None
        if process is None:
            return
        try:
            conn.send(("stop",))
        except (OSError, ValueError, EOFError):
            pass
        process.join(STOP_S)
        if process.is_alive():
            process.terminate()
            process.join(STOP_S)
        _PIDS.discard(process.pid)
        try:
            conn.close()
        except OSError:
            pass
        self._gone(conn)

    def stop(self) -> None:
        """End the process (the app has been closed a while): its memory goes back to the
        system with it; the next picture starts it again."""
        with self._lock:
            if self._process is not None:
                log.info("the app's pictures: their process ends (%s)", self._process.pid)
            self._ended()

    def pid(self) -> int | None:
        """The process's id while it runs (what Status → Diagnose → Load calls it by)."""
        process = self._process
        going = process is not None and self._broken is not self._conn and process.is_alive()
        return process.pid if going else None


class _Logs(logging.Handler):
    """The process's log records go to the app's log, through the pipe."""

    def __init__(self, send: Any) -> None:
        super().__init__(logging.INFO)
        self.send = send

    def emit(self, record: logging.LogRecord) -> None:
        try:
            self.send(("log", record.levelno, record.name, self.format(record)))
        except Exception:  # noqa: BLE001 - a record that does not go across is lost
            pass


def child(conn: Any) -> None:
    """The pictures' process: the jobs asked for, one at a time in the order they came, Design's
    and Look's picture of the theme (:data:`FIRST`) before those for lists; until it is told to
    stop or the app's process is gone. One at a time: two drawings at once, in one process,
    from caches that are not filled yet, came out a level apart here and there in their lights
    (a ring's, a bar's), where the same drawing alone is always the same: here they are
    always as drawn alone, and a picture takes no more than a quarter of a second (Lumen's)."""
    import heapq
    import itertools

    from libre_panel import i18n, light

    light.fewer_heaps()  # (a process started anew: before its threads)
    send_lock = threading.Lock()

    def send(message: Any) -> None:
        with send_lock:
            conn.send(message)

    root = logging.getLogger()
    root.handlers[:] = [_Logs(send)]
    root.setLevel(logging.INFO)
    queue: list[tuple[int, int, int, str, tuple[Any, ...]]] = []
    order = itertools.count()
    came = threading.Condition()
    done = threading.Event()

    def draw() -> None:
        while True:
            with came:
                came.wait_for(lambda: queue or done.is_set())
                if done.is_set():
                    return
                _first, _n, ident, job, args = heapq.heappop(queue)
            try:
                value = _run(job, args, None)
            except Exception as exc:  # noqa: BLE001 - the app hears what it was
                answer: tuple[Any, ...] = ("done", ident, False, (type(exc).__name__, str(exc)))
            else:
                answer = ("done", ident, True, value)
            try:
                send(answer)
            except (OSError, ValueError, EOFError):
                return
            except Exception as exc:  # noqa: BLE001 - what it gave does not go across
                send(("done", ident, False, (type(exc).__name__, str(exc))))

    threading.Thread(target=draw, name="pictures-draw", daemon=True).start()
    send(("ready",))
    while True:
        try:
            message = conn.recv()
        except (EOFError, OSError):
            break
        if message[0] == "stop":
            break
        if message[0] == "language":
            i18n.set_language(message[1])
        elif message[0] == "job":
            _kind, ident, job, args = message
            with came:
                heapq.heappush(queue, (0 if job in FIRST else 1, next(order), ident, job, args))
                came.notify()
    done.set()
    with came:
        came.notify_all()
    conn.close()
    os._exit(0)  # (what it was drawing is nobody's now: no waiting for it)
