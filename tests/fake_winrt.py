"""The media controls of Windows by hand, for the tests of the media source: a session
manager, sessions with their properties and timeline, covers, and the events they send.
No WinRT package is needed; ``FakeWinrt`` stands in for what ``media._winrt()`` gives."""

import itertools
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace


class EventSource:
    """An object with WinRT events: ``add_<name>(handler)`` gives a token,
    ``remove_<name>(token)`` takes it back. ``fire`` is what Windows does."""

    def __init__(self):
        self.handlers = {}  # event name -> {token: handler}
        self.refused = set()  # events this object cannot be subscribed to
        self.history = []  # ("add" | "remove", name), in order
        self._tokens = itertools.count(1)

    def __getattr__(self, attribute):
        verb, _, name = attribute.partition("_")
        if verb == "add" and name:
            return lambda handler: self._add(name, handler)
        if verb == "remove" and name:
            return lambda token: self._remove(name, token)
        raise AttributeError(attribute)

    def _add(self, name, handler):
        if name in self.refused:
            raise OSError(f"no event {name}")
        token = next(self._tokens)
        self.handlers.setdefault(name, {})[token] = handler
        self.history.append(("add", name))
        return token

    def _remove(self, name, token):
        del self.handlers[name][token]
        self.history.append(("remove", name))

    def listening(self, name):
        """How many handlers listen to the event."""
        return len(self.handlers.get(name, {}))

    def fire(self, name):
        """Windows reports the event, from a thread of its own."""
        for handler in list(self.handlers.get(name, {}).values()):
            handler(self, None)


class FakeBuffer(bytearray):
    """winrt's Buffer: room for ``size`` bytes, and ``length`` of them filled."""

    length = 0


class FakeStream:
    def __init__(self, data):
        self.data, self.size = data, len(data)

    async def read_async(self, buffer, count, options):
        buffer[: len(self.data)] = self.data
        buffer.length = len(self.data)
        return buffer


class FakeThumbnail:
    """A cover that can be opened; ``opened`` counts how often it was."""

    def __init__(self, data, fails=False):
        self.data, self.fails, self.opened = data, fails, 0

    async def open_read_async(self):
        self.opened += 1
        if self.fails:
            raise OSError("no cover")
        return FakeStream(self.data)


class FakeSession(EventSource):
    """One player's session. Times are in seconds; ``position`` counts from ``start``."""

    def __init__(
        self,
        app_id="Spotify.exe",
        title="Song",
        artist="Band",
        album="Album",
        thumbnail=None,
        status=4,  # PLAYING
        position=60.0,
        length=200.0,
        start=0.0,
        updated=None,
    ):
        super().__init__()
        self.source_app_user_model_id = app_id
        self.title, self.artist, self.album, self.thumbnail = title, artist, album, thumbnail
        self.status, self.position, self.length, self.start = status, position, length, start
        self.updated = updated if updated is not None else datetime.now(UTC)
        self.reads = 0  # how often the properties were asked for
        self.fail_next = False

    async def try_get_media_properties_async(self):
        self.reads += 1
        if self.fail_next:
            self.fail_next = False
            raise OSError("the media controls hang")
        return SimpleNamespace(
            title=self.title,
            artist=self.artist,
            album_title=self.album,
            album_artist="",
            thumbnail=self.thumbnail,
        )

    def get_playback_info(self):
        return SimpleNamespace(playback_status=self.status)

    def get_timeline_properties(self):
        return SimpleNamespace(
            start_time=timedelta(seconds=self.start),
            end_time=timedelta(seconds=self.start + self.length),
            position=timedelta(seconds=self.start + self.position),
            last_updated_time=self.updated,
        )


class FakeManager(EventSource):
    def __init__(self):
        super().__init__()
        self.current = None

    def get_current_session(self):
        return self.current


class FakeWinrt:
    """What ``media._winrt()`` returns: the manager (one, as in Windows), the buffer and
    the apartment calls."""

    def __init__(self):
        self.manager = FakeManager()
        self.requests = 0  # how often a manager was asked for
        self.apartments = []
        outer = self

        class Manager:
            @staticmethod
            async def request_async():
                outer.requests += 1
                return outer.manager

        self.Manager = Manager
        self.Buffer = FakeBuffer
        self.InputStreamOptions = SimpleNamespace(NONE=0, READ_AHEAD=1)
        self.ApartmentType = SimpleNamespace(MULTI_THREADED="mta")

    def init_apartment(self, kind):
        self.apartments.append(kind)
