"""The screen on Wayland (mirror.py): no program may look at it; the desktop shares it through
PipeWire, asked over D-Bus (jeepney), and GStreamer (``gst-launch-1.0`` with ``pipewiresrc``,
the desktop's own) reads the stream.

- **GNOME** (Mutter's screen cast, ``org.gnome.Mutter.ScreenCast``): a monitor by its connector
  (listed through ``org.gnome.Mutter.DisplayConfig``: :func:`monitors`), or a monitor of the
  panel's own (``RecordVirtual``: GNOME makes it as the capture starts, at the panel's size, and
  takes it away as it ends); no dialog.
- **Any other desktop** (KDE, Sway, Hyprland ...) through the screen-sharing portal
  (``org.freedesktop.portal.ScreenCast``): the desktop's dialog asks which monitor or window, or
  offers a new virtual output (KDE: 1920x1080 until Plasma 6.8, cropped to the panel). The
  choice is kept (``persist_mode``): the next time it asks no more.

A capture runs in a program of its own (wayland_feed.py: ``libre-panel wayland-feed``): it
holds the desktop's session for as long as it runs (the session ends with its connection),
starts GStreamer on the stream and writes the panel's frames, raw and fitted, to its output,
which is ffmpeg's input. When the panel shows something else, it ends, and so does the session.
"""

from __future__ import annotations

import logging
import shutil
import sys
from typing import Any

from libre_panel import capture

log = logging.getLogger(__name__)

MUTTER_CAST = "org.gnome.Mutter.ScreenCast"
MUTTER_DISPLAY = "org.gnome.Mutter.DisplayConfig"
PORTAL = "org.freedesktop.portal.Desktop"
ASK = "ask"  # the id of a monitor or window the desktop's dialog asks for


def jeepney_found() -> bool:
    try:
        import jeepney  # noqa: F401
    except ImportError:
        return False
    return True


def gstreamer() -> str | None:
    """``gst-launch-1.0``, where it is (its pipewiresrc comes with the desktop's PipeWire)."""
    return shutil.which("gst-launch-1.0")


def _connection() -> Any:
    from jeepney.io.blocking import open_dbus_connection

    return open_dbus_connection(bus="SESSION", enable_fds=True)


def has_name(connection: Any, name: str) -> bool:
    from jeepney import DBusAddress, new_method_call

    bus = DBusAddress("/org/freedesktop/DBus", bus_name="org.freedesktop.DBus",
                      interface="org.freedesktop.DBus")  # fmt: skip
    reply = connection.send_and_get_reply(new_method_call(bus, "NameHasOwner", "s", (name,)),
                                          timeout=2.0)  # fmt: skip
    return bool(reply.body[0])


def desktop() -> dict[str, bool]:
    """Which ways there are here: ``gnome`` (Mutter's screen cast), ``portal``."""
    if not jeepney_found():
        return {"gnome": False, "portal": False}
    try:
        connection = _connection()
    except (OSError, ValueError) as exc:
        log.info("wayland: no session bus (%s)", exc)
        return {"gnome": False, "portal": False}
    try:
        return {"gnome": has_name(connection, MUTTER_CAST), "portal": has_name(connection, PORTAL)}
    except Exception as exc:  # noqa: BLE001 - a bus that answers oddly is no way in
        log.info("wayland: the session bus did not answer (%s)", exc)
        return {"gnome": False, "portal": False}
    finally:
        connection.close()


def virtual_state() -> dict[str, Any]:
    """For the app (extend.state): a monitor of the panel's own is there for the asking where
    GNOME or the portal is, with GStreamer and jeepney; ``missing`` says what is not."""
    if not jeepney_found():
        return {"ready": False, "missing": "jeepney"}
    if gstreamer() is None:
        return {"ready": False, "missing": "gstreamer"}
    ways = desktop()
    if not (ways["gnome"] or ways["portal"]):
        return {"ready": False, "missing": "portal"}
    return {"ready": True, "missing": "", "gnome": ways["gnome"]}


def monitors() -> list[capture.Monitor]:
    """GNOME's monitors (DisplayConfig.GetCurrentState): connector, place, size (its mode's
    pixels), the main one; [] on other desktops (their dialog lists them)."""
    if not jeepney_found():
        return []
    try:
        connection = _connection()
    except (OSError, ValueError):
        return []
    try:
        from jeepney import DBusAddress, new_method_call

        if not has_name(connection, MUTTER_DISPLAY):
            return []
        address = DBusAddress("/org/gnome/Mutter/DisplayConfig", bus_name=MUTTER_DISPLAY,
                              interface=MUTTER_DISPLAY)  # fmt: skip
        reply = connection.send_and_get_reply(new_method_call(address, "GetCurrentState"),
                                              timeout=2.0)  # fmt: skip
        return gnome_monitors(reply.body)
    except Exception as exc:  # noqa: BLE001 - listed or not, the dialog way stays
        log.info("wayland: GNOME's monitors not listed (%s)", exc)
        return []
    finally:
        connection.close()


def gnome_monitors(state: tuple[Any, ...]) -> list[capture.Monitor]:
    """The monitors of DisplayConfig's state ``(serial, monitors, logical_monitors, props)``:
    each logical monitor's place and its monitor's current mode, by connector."""
    _serial, physical, logical, _props = state
    sizes: dict[str, tuple[int, int, str]] = {}
    for (connector, _vendor, _product, _serial_no), modes, props in physical:
        current = next((m for m in modes if _variant(m[6].get("is-current"))), None)
        if current is None:
            continue
        name = _variant(props.get("display-name")) or connector
        sizes[connector] = (int(current[1]), int(current[2]), str(name))
    found = []
    for x, y, _scale, _transform, primary, monitors_of, _props in logical:
        for connector, *_rest in monitors_of:
            if connector in sizes:
                width, height, name = sizes[connector]
                virtual = connector.startswith("Meta-")  # (Mutter's own, as RecordVirtual's)
                found.append(capture.Monitor(connector, name, int(x), int(y), width, height,
                                             bool(primary), virtual))  # fmt: skip
    return found


def _variant(value: Any) -> Any:
    """A D-Bus variant's value (jeepney gives (signature, value)), else the value."""
    if isinstance(value, tuple) and len(value) == 2 and isinstance(value[0], str):
        return value[1]
    return value


def feeder_command() -> list[str]:
    """How the app starts its own capture program (wayland_feed.py)."""
    if getattr(sys, "frozen", False):
        return [sys.executable, "wayland-feed"]
    return [sys.executable, "-m", "libre_panel", "wayland-feed"]


def capture_of(source: Any, fps: int, size: tuple[int, int] | None = None) -> capture.Capture:
    """ffmpeg's input for ``source`` (mirror.Source) here: raw frames of ``size`` (the panel's)
    from the capture program, which fits them as ``source.fit`` says."""
    from libre_panel.mirror import MirrorError

    if not jeepney_found():
        raise MirrorError("on Wayland a panel shows the screen with the jeepney library")
    if gstreamer() is None:
        raise MirrorError("on Wayland a panel shows the screen through GStreamer "
                          "(gst-launch-1.0 and its PipeWire plugin): install them")  # fmt: skip
    if size is None:
        raise MirrorError("the panel's size is not known")
    width, height = size
    ident = source.id or ASK
    command = [*feeder_command(), "--kind", source.kind, "--id", ident,
               "--size", f"{width}x{height}", "--fps", str(fps), "--fit", source.fit]  # fmt: skip
    if not source.cursor:
        command.append("--no-cursor")
    inputs = ("-f", "rawvideo", "-pix_fmt", "bgr0", "-video_size", f"{width}x{height}",
              "-framerate", str(fps), "-i", "pipe:0")  # fmt: skip
    return capture.Capture(inputs, feeder=tuple(command), label=source.name or source.kind)
