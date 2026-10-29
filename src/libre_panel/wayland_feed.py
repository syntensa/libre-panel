"""The capture program on Wayland (``libre-panel wayland-feed``, started by wayland.py): it asks
the desktop for the screen, holds its session while it runs, and writes the panel's frames to
its output, raw (BGRx), fitted to the panel's size, at the panel's frames a second. Nothing
else goes to its output: what it says goes to its error output.

1. **The stream.** GNOME: Mutter's screen cast, a session with a monitor by its connector
   (``RecordMonitor``) or a monitor of the panel's own (``RecordVirtual``: Mutter makes it at
   the size the stream asks for, the panel's); started, its stream says its PipeWire node.
   Elsewhere: the screen-sharing portal: a session, the sources (a monitor, a window, a virtual
   output; the dialog asks, its choice kept for the next time: ``persist_mode``), started; the
   PipeWire node of the stream and the PipeWire connection the portal opens for it (a file
   descriptor).
2. **The pictures.** ``gst-launch-1.0``: ``pipewiresrc`` on that node, converted, cropped and
   scaled as ``--fit`` says (``contain``: whole, on black; ``cover``: to the edges; ``stretch``),
   at ``--fps``, to this program's output. It ends with this program (Linux kills it with its
   parent).
3. **The end.** When GStreamer ends, the session is stopped; when the desktop closes the session
   (the person stopped sharing), GStreamer is ended; either way this program ends, and ffmpeg
   with its input.
"""

from __future__ import annotations

import argparse
import itertools
import json
import os
import signal
import subprocess
import sys
from typing import Any

MUTTER = "org.gnome.Mutter.ScreenCast"
PORTAL = "org.freedesktop.portal.Desktop"
TYPES = {"monitor": 1, "window": 2, "virtual": 4}
DIALOG_S = 300.0  # the dialog waits on the person
ANSWER_S = 10.0
TOKENS = "wayland-screencast.json"  # the portal's restore tokens, by kind, beside config.toml


class Refused(Exception):
    """The desktop gave no stream: said for the log (the person cancelled, ...)."""


def say(text: str) -> None:
    print(f"wayland-feed: {text}", file=sys.stderr, flush=True)


# -- D-Bus ------------------------------------------------------------------------------------


def _call(connection: Any, address: Any, method: str, signature: str = "",
          body: tuple[Any, ...] = (), timeout: float = ANSWER_S) -> tuple[Any, ...]:  # fmt: skip
    from jeepney import new_method_call
    from jeepney.wrappers import unwrap_msg

    reply = connection.send_and_get_reply(new_method_call(address, method, signature, body),
                                          timeout=timeout)  # fmt: skip
    return unwrap_msg(reply)


def _listen(connection: Any, rule: Any) -> None:
    from jeepney.bus_messages import message_bus

    connection.send_and_get_reply(message_bus.AddMatch(rule), timeout=ANSWER_S)


def _value(variant: Any) -> Any:
    if isinstance(variant, tuple) and len(variant) == 2 and isinstance(variant[0], str):
        return variant[1]
    return variant


def mutter_stream(connection: Any, kind: str, connector: str,
                  cursor: bool) -> tuple[int, None, None, Any]:  # fmt: skip
    """GNOME: ``(node, None, None, session)``: a monitor by its connector, or the panel's own."""
    from jeepney import DBusAddress, MatchRule

    cast = DBusAddress("/org/gnome/Mutter/ScreenCast", bus_name=MUTTER, interface=MUTTER)
    (session_path,) = _call(connection, cast, "CreateSession", "a{sv}", ({},))
    session = DBusAddress(session_path, bus_name=MUTTER, interface=f"{MUTTER}.Session")
    options = {"cursor-mode": ("u", 1 if cursor else 0)}  # (1: drawn into the picture)
    if kind == "virtual":
        (stream_path,) = _call(connection, session, "RecordVirtual", "a{sv}", (options,))
    else:
        (stream_path,) = _call(connection, session, "RecordMonitor", "sa{sv}",
                               (connector, options))  # fmt: skip
    rule = MatchRule(type="signal", interface=f"{MUTTER}.Stream", member="PipeWireStreamAdded",
                     path=stream_path)  # fmt: skip
    _listen(connection, rule)
    with connection.filter(rule) as added:
        _call(connection, session, "Start")
        message = connection.recv_until_filtered(added, timeout=ANSWER_S)
    return int(message.body[0]), None, None, session


def _tokens_file() -> Any:
    from libre_panel.config import config_dir

    return config_dir() / TOKENS


def _restore_token(kind: str) -> str:
    try:
        return str(json.loads(_tokens_file().read_text(encoding="utf-8")).get(kind, ""))
    except (OSError, ValueError, AttributeError):
        return ""


def _keep_token(kind: str, token: str) -> None:
    path = _tokens_file()
    try:
        known = json.loads(path.read_text(encoding="utf-8"))
        known = known if isinstance(known, dict) else {}
    except (OSError, ValueError):
        known = {}
    known[kind] = token
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(known), encoding="utf-8")
    except OSError as exc:
        say(f"the desktop's choice is not kept ({exc})")


def portal_stream(connection: Any, kind: str,
                  cursor: bool) -> tuple[int, int, tuple[int, int] | None, Any]:  # fmt: skip
    """Elsewhere: ``(node, fd, size of the stream, session)`` through the portal; the dialog
    asks the person which source, unless a kept choice (restore token) answers for them."""
    from jeepney import DBusAddress, MatchRule

    portal = DBusAddress("/org/freedesktop/portal/desktop", bus_name=PORTAL,
                         interface="org.freedesktop.portal.ScreenCast")  # fmt: skip
    sender = connection.unique_name.lstrip(":").replace(".", "_")
    count = itertools.count(1)

    def request(method: str, signature: str, body: Any, timeout: float = ANSWER_S) -> dict:
        token = f"libre_panel_{os.getpid()}_{next(count)}"
        path = f"/org/freedesktop/portal/desktop/request/{sender}/{token}"
        rule = MatchRule(type="signal", interface="org.freedesktop.portal.Request",
                         member="Response", path=path)  # fmt: skip
        _listen(connection, rule)
        with connection.filter(rule) as answered:
            _call(connection, portal, method, signature, body(token))
            message = connection.recv_until_filtered(answered, timeout=timeout)
        code, results = message.body
        if code != 0:
            raise Refused("cancelled" if code == 1 else f"refused ({code})")
        return results

    results = request(
        "CreateSession",
        "a{sv}",
        lambda token: (
            {"handle_token": ("s", token), "session_handle_token": ("s", f"{token}_session")},
        ),
    )
    session_path = _value(results["session_handle"])
    choose = {"types": ("u", TYPES[kind]), "multiple": ("b", False),
              "cursor_mode": ("u", 2 if cursor else 1), "persist_mode": ("u", 2)}  # fmt: skip
    kept = _restore_token(kind)
    if kept:
        choose["restore_token"] = ("s", kept)
    request("SelectSources", "oa{sv}",
            lambda token: (session_path, {**choose, "handle_token": ("s", token)}))  # fmt: skip
    results = request("Start", "osa{sv}",
                      lambda token: (session_path, "", {"handle_token": ("s", token)}),
                      timeout=DIALOG_S)  # fmt: skip
    streams = _value(results.get("streams")) or []
    if not streams:
        raise Refused("no stream")
    node, properties = streams[0]
    size = _value(properties.get("size"))
    token = _value(results.get("restore_token"))
    if token:
        _keep_token(kind, str(token))
    (remote,) = _call(connection, portal, "OpenPipeWireRemote", "oa{sv}", (session_path, {}))
    session = DBusAddress(session_path, bus_name=PORTAL, interface="org.freedesktop.portal.Session")
    return int(node), remote.to_raw_fd(), tuple(size) if size else None, session


# -- GStreamer ------------------------------------------------------------------------------------


def cover_crop(stream: tuple[int, int], size: tuple[int, int]) -> tuple[int, int, int, int]:
    """``(left, right, top, bottom)`` cropping ``stream`` to ``size``'s proportions, centred."""
    sw, sh = stream
    width, height = size
    if sw * height > sh * width:  # wider: off the sides
        keep = round(sh * width / height)
        left = (sw - keep) // 2
        return left, sw - keep - left, 0, 0
    keep = round(sw * height / width)
    top = (sh - keep) // 2
    return 0, 0, top, sh - keep - top


def gst_command(gst: str, node: int, fd: int | None, stream: tuple[int, int] | None,
                size: tuple[int, int], fps: int, fit: str, sized: bool) -> list[str]:  # fmt: skip
    """gst-launch-1.0's pipeline: the stream of ``node`` (through ``fd`` where the portal opened
    it; ``sized``: asked at the panel's size, Mutter's monitor of its own) to raw BGRx frames of
    ``size`` on its output, ``fps`` a second, fitted as ``fit`` says."""
    width, height = size
    source = ["pipewiresrc", f"path={node}", "do-timestamp=true", "keepalive-time=1000",
              "always-copy=true"]  # fmt: skip
    if fd is not None:
        source.append(f"fd={fd}")
    parts = [source]
    if sized:
        parts.append([f"video/x-raw,width={width},height={height}"])
    parts.append(["videoconvert"])
    if fit == "cover" and stream:
        left, right, top, bottom = cover_crop(stream, size)
        parts.append(["videocrop", f"left={left}", f"right={right}", f"top={top}",
                      f"bottom={bottom}"])  # fmt: skip
    parts.append(["videoscale", f"add-borders={'true' if fit == 'contain' else 'false'}"])
    parts.append([f"video/x-raw,format=BGRx,width={width},height={height},pixel-aspect-ratio=1/1"])
    parts.append(["videorate"])
    parts.append([f"video/x-raw,framerate={fps}/1"])
    parts.append(["fdsink", "fd=1", "sync=false"])
    command = [gst, "-q"]
    for n, part in enumerate(parts):
        command += (["!"] if n else []) + part
    return command


def _with_parent() -> None:
    """GStreamer ends with this program (Linux: a signal when its parent dies)."""
    try:
        import ctypes

        ctypes.CDLL("libc.so.6", use_errno=True).prctl(1, signal.SIGKILL)  # PR_SET_PDEATHSIG
    except OSError:
        pass


# -- the program --------------------------------------------------------------------------------


def add_arguments(parser: argparse.ArgumentParser) -> None:
    """The program's options (cli.py's ``wayland-feed`` takes them as they are)."""
    parser.add_argument("--kind", choices=sorted(TYPES), required=True)
    parser.add_argument("--id", default="ask")
    parser.add_argument("--size", required=True)
    parser.add_argument("--fps", type=int, default=30)
    parser.add_argument("--fit", choices=("contain", "cover", "stretch"), default="contain")
    parser.add_argument("--no-cursor", action="store_true")


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(prog="libre-panel wayland-feed")
    add_arguments(parser)
    return run(parser.parse_args(argv))


def run(args: argparse.Namespace) -> int:
    try:
        width, height = (int(part) for part in args.size.lower().split("x"))
    except ValueError:
        say("--size: WIDTHxHEIGHT")
        return 2
    from shutil import which

    gst = which("gst-launch-1.0")
    if gst is None:
        say("no gst-launch-1.0")
        return 2
    from jeepney.io.blocking import open_dbus_connection

    connection = open_dbus_connection(bus="SESSION", enable_fds=True)
    session = None
    cursor = not args.no_cursor
    try:
        from libre_panel.wayland import ASK, MUTTER_CAST, has_name

        gnome = has_name(connection, MUTTER_CAST)
        if gnome and (args.kind == "virtual" or (args.kind == "monitor" and args.id != ASK)):
            node, fd, stream, session = mutter_stream(connection, args.kind, args.id, cursor)
            sized = args.kind == "virtual"
        else:
            node, fd, stream, session = portal_stream(connection, args.kind, cursor)
            sized = False
        command = gst_command(gst, node, fd, stream, (width, height), args.fps, args.fit, sized)
        say(f"stream {node}{f' ({stream[0]}x{stream[1]})' if stream else ''}")
        feeding = subprocess.Popen(command, pass_fds=(fd,) if fd is not None else (),
                                   preexec_fn=_with_parent)  # fmt: skip
        if fd is not None:
            os.close(fd)
        return _until_ended(connection, session, feeding)
    except Refused as exc:
        say(f"the desktop gave no screen: {exc}")
        return 3
    except Exception as exc:  # noqa: BLE001 - said, and the panel shows why
        say(f"{type(exc).__name__}: {exc}")
        return 1
    finally:
        _close(connection, session)


def _until_ended(connection: Any, session: Any, feeding: subprocess.Popen) -> int:
    from jeepney import MatchRule

    rule = MatchRule(type="signal", member="Closed", path=session.object_path)
    _listen(connection, rule)
    try:
        with connection.filter(rule) as closed:
            while feeding.poll() is None:
                try:
                    connection.recv_until_filtered(closed, timeout=1.0)
                except TimeoutError:
                    continue
                say("the desktop stopped sharing the screen")
                break
    finally:
        if feeding.poll() is None:
            feeding.kill()
        feeding.wait()
    return 0


def _close(connection: Any, session: Any) -> None:
    try:
        if session is not None:
            method = "Stop" if session.interface.startswith(MUTTER) else "Close"
            _call(connection, session, method, timeout=2.0)
    except Exception:  # noqa: BLE001 - the session ends with the connection anyway
        pass
    finally:
        connection.close()


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main(sys.argv[1:]))
