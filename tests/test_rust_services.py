"""rust/services against the Python app (docs/RUST.md): the host between the services and
the main loop (plugins/host.py), as the Rust app has it."""

from __future__ import annotations

import json
import random
from typing import Any

from rust_check import run

from libre_panel.plugins import host as hosts
from libre_panel.plugins.loader import PluginError
from libre_panel.sensors.base import Reading


def test_the_services_host_holds_what_pythons_holds() -> None:
    """plugins/host.py's PluginHost: readings and states published and taken back, the
    theme the services ask for (by priority, then the latest, in the modes each request
    counts in), the mode and who set it, the transition of a switch, the messages kept for
    the main loop, a service that stops and leaves nothing; the mode changes a listener
    hears; and a service's options from its defaults and config.toml's."""
    rng = random.Random(0x5E81)
    services = ["game", "autopilot", "timer", "notices", None]
    themes = ["arena", "slate", "lumen", "", None]
    modes = ["game", "music", None, None, "*"]
    keys = ["game.fps", "timer.left", "notice.state", "a.b"]
    runs = []
    for _ in range(300):
        steps: list[list[Any]] = []
        for _ in range(rng.randrange(1, 40)):
            kinds = ["publish", "state", "unpublish", "theme", "theme", "theme", "mode", "mode",
                     "forget", "notify", "take_toasts", "take_transition"]  # fmt: skip
            kind = rng.choice(kinds)
            if kind == "publish":
                value = rng.choice([1, 2.5, "text", None, True])
                steps.append([kind, rng.choice(keys), value, rng.choice(["", "fps"]), "Label",
                              rng.choice(services)])  # fmt: skip
            elif kind == "state":
                value = rng.choice([{"a": [1, "ü", None]}, [1, 2.5], None, {}, 5, "text", True])
                steps.append([kind, rng.choice(keys), value, rng.choice(services), "State"])
            elif kind == "unpublish":
                steps.append([kind, rng.choice(keys)])
            elif kind == "theme":
                steps.append([kind, rng.choice(themes), rng.choice(services[:4]),
                              rng.choice([None, "fade", "slide"]), rng.choice([0, 0, 5, -1]),
                              rng.choice(modes)])  # fmt: skip
            elif kind == "mode":
                steps.append([kind, rng.choice(modes[:4]), rng.choice(services),
                              rng.choice([None, None, "fade"])])  # fmt: skip
            elif kind == "forget":
                steps.append([kind, rng.choice(services[:4])])
            elif kind == "notify":
                steps.append([kind, f"message {rng.randrange(99)}", rng.choice(["", "music"]),
                              rng.randrange(3), "notices"])  # fmt: skip
            else:
                steps.append([kind])
        if rng.random() < 0.2:  # (more messages than the host keeps)
            steps += [["notify", f"m{n}", "", 0, "notices"] for n in range(25)]
            steps.append(["take_toasts"])
        runs.append(steps)

    schema = [["limit", "int", 5], ["rate", "number", 2.5], ["on", "bool", True],
              ["name", "string", "x"], ["list", "list", ["a"]]]  # fmt: skip
    values = [1, 2.5, True, False, "text", ["a", 1], {"a": 1}, None, 10**30, -0.0]
    options = []
    for _ in range(400):
        given = {key: rng.choice(values)
                 for key in rng.sample(["limit", "rate", "on", "name", "list", "other", "cloud",
                                        "cloud_token"], rng.randrange(0, 4))}  # fmt: skip
        options.append([schema, given, rng.choice(["services.game", "services.agents"])])
    sent = [[schema, json.dumps(given), where] for schema, given, where in options]
    [ours] = run([{"op": "services_host", "runs": runs, "options": sent}])

    def value_of(value: Any) -> Any:
        return {"float": repr(value)} if isinstance(value, float) else value

    seen = {"themes": 0, "modes": 0, "errors": 0, "toasts": 0, "unknown": 0, "heard": 0}
    for steps, (found, heard) in zip(runs, ours["runs"], strict=True):
        host = hosts.PluginHost(clock=lambda: 12.5)
        modes_heard: list[Any] = []
        host.listen("mode-changed", lambda mode, heard=modes_heard: heard.append(mode))
        for step, got in zip(steps, found, strict=True):
            kind, said = step[0], None
            if kind == "publish":
                host.publish(Reading(step[1], step[2], step[3], step[4]), owner=step[5])
            elif kind == "state":
                try:
                    host.publish_state(step[1], step[2], owner=step[3], label=step[4])
                except TypeError as exc:
                    said = {"error": str(exc)}
                    seen["errors"] += 1
            elif kind == "unpublish":
                host.unpublish(step[1])
            elif kind == "theme":
                host.request_theme(step[1], by=step[2], transition=step[3], priority=step[4],
                                   mode=step[5])  # fmt: skip
            elif kind == "mode":
                host.set_mode(step[1], by=step[2], transition=step[3])
            elif kind == "forget":
                host.forget(step[1])
            elif kind == "notify":
                delivery = host.notify(hosts.Toast(step[1], kind=step[2], rank=step[3],
                                                   service=step[4]))  # fmt: skip
                said = [delivery.shown, delivery.reason]
            elif kind == "take_toasts":
                said = [[t.text, t.kind, t.rank, t.service] for t in host.take_toasts()]
                seen["toasts"] += len(said) == hosts.PluginHost.MAX_TOASTS
            else:
                said = host.take_transition()
            published = [[key, value_of(r.value), r.unit, r.label, r.stamp]
                         for key, r in host.published().items()]  # fmt: skip
            wanted = [said, published, host.theme, host.theme_owner, host.mode, host.mode_owner,
                      host.switches]  # fmt: skip
            assert got == wanted, (steps, step)
            seen["themes"] += host.theme is not None
            seen["modes"] += host.mode is not None
        host.close()
        assert heard == modes_heard, steps
        seen["heard"] += len(heard)
    for (_, given, where), got in zip(options, ours["options"], strict=True):
        try:
            wanted = hosts.apply_options({key: (kind, default) for key, kind, default in schema},
                                         given, where)  # fmt: skip
        except PluginError as exc:
            assert got == {"error": str(exc)}, given
            seen["errors"] += 1
            continue
        assert "error" not in got, (given, got)
        assert got[0] == [[key, repr(value)] for key, value in wanted.items()], given
        seen["unknown"] += len(got[1])
    assert all(count > 30 for count in seen.values()), seen


class _Host:
    """A service's host for a test: what it published last, and what it said."""

    def __init__(self) -> None:
        self.published: dict[str, list[Any]] = {}
        self.said: list[str] = []

    def publish(self, key: str, value: Any, unit: str = "", label: str = "") -> None:
        self.published[key] = [key, value, unit, label]

    def notify(self, text: str, **_: Any) -> None:
        self.said.append(text)

    def on(self, *_: Any) -> None:
        pass


def test_the_timer_counts_as_timer_py_counts() -> None:
    """services/timer.py: started, paused and set back at moments of a clock, the time that
    is up (a plain timer; focus and break in turn), what it says then and its readings; and
    the clock's text of seconds."""
    from libre_panel import i18n
    from libre_panel.services import timer

    rng = random.Random(0x7133)
    runs = []
    for _ in range(300):
        minutes = rng.choice([25, 0.05, 0.1, 1, 0, 90])
        breaks = rng.choice([0, 0, 0.05, 5, -1])
        now, steps = rng.uniform(0, 1000), []
        for _ in range(rng.randrange(1, 60)):
            now += rng.choice([0.2, 0.2, 1.0, 2.9, 3.0, 30.0, 400.0])
            kind = rng.choice(["toggle", "tick", "tick", "tick", "reset"])
            steps.append([kind, now])
        runs.append([minutes, breaks, steps])
    seconds = [0, 0.1, 1, 59.0, 59.01, 60, 61.5, 3599.5, 3600, 3661.2, 86400.0, -5, 1e7, 0.999]
    for language in ("en", "de"):
        [ours] = run([{"op": "timer", "locale": str(i18n.LOCALE_DIR), "language": language,
                       "runs": runs, "seconds": seconds}])  # fmt: skip
        assert ours["texts"] == [timer.clock_text(s) for s in seconds]
        seen = {"up": 0, "break": 0, "over": 0, "paused": 0}
        i18n.set_language(language)
        try:
            for (minutes, breaks, steps), found in zip(runs, ours["runs"], strict=True):
                now = [0.0]
                host = _Host()
                options = {"minutes": minutes, "break_minutes": breaks, "notice": True}
                service = timer.Timer(host, options, clock=lambda now=now: now[0])
                for (kind, at), (told, readings) in zip(steps, found, strict=True):
                    now[0] = at
                    host.said.clear()
                    {"toggle": service.toggle, "reset": service.reset, "tick": service.tick}[kind]()
                    assert ([told] if told else []) == host.said, (minutes, breaks, steps)
                    rows = [list(row) for row in host.published.values()]
                    for row in rows:
                        if isinstance(row[1], float):
                            row[1] = {"float": repr(row[1])}
                    assert readings == rows, (minutes, breaks, steps, at)
                    if told:
                        seen["up" if breaks <= 0 else "break" if service.phase == "break"
                             else "over"] += 1  # fmt: skip
                    seen["paused"] += kind == "toggle" and not service.running
        finally:
            i18n.set_language("en")
        assert all(count > 20 for count in seen.values()), seen


def test_power_is_counted_as_power_py_counts_it(tmp_path: Any) -> None:
    """services/power.py: the watts of the sensors named and the rest, the energy of the day
    and the month they add up to between samples (not over a pause), their cost, a new day
    and a new month, the totals kept before and those it keeps."""
    from datetime import datetime, timedelta
    from types import SimpleNamespace

    from libre_panel.services import power

    rng = random.Random(0x90E4)
    runs = []
    for n in range(300):
        sensors = rng.choice([["cpu.power", "gpu.power"], ["cpu.power"], [], ["psu.out", " ", 5]])
        rest = rng.choice([0, 50, 62.5, -10])
        price = rng.choice([0, 0.3, 0.2875, -1])
        wall = datetime(2026, rng.randrange(1, 13), rng.randrange(1, 29), rng.randrange(24),
                        rng.randrange(60), rng.randrange(60))  # fmt: skip
        day = wall.strftime("%Y-%m-%d")
        totals = json.dumps({
            "day": rng.choice([day, day, day, "2020-01-01", 5]),
            "today_wh": rng.choice([12.5, 7, 0.125, "x", None, True, 1e400]),
            "month": rng.choice([day[:7], day[:7], "2020-01"]),
            "month_wh": rng.choice([345.678, 0, None]),
        })  # fmt: skip
        kept = rng.choice([None, "", "[1]", "{broken", totals, totals, totals, totals])
        now, steps = rng.uniform(0, 1000), []
        for _ in range(rng.randrange(1, 40)):
            pause = rng.choice([1.0, 1.0, 1.0, 0.5, 10.0, 10.5, 0.0, 3600.0, 86400.0])
            now += pause
            wall += timedelta(seconds=pause)
            readings = {key: rng.choice([65.5, 120, 0, -5, None, True, "9", 2**60, 33.333])
                        for key in ("cpu.power", "gpu.power", "psu.out", "5")
                        if rng.random() < 0.8}  # fmt: skip
            steps.append([now, [wall.year, wall.month, wall.day, wall.hour, wall.minute,
                                wall.second, 0], readings])  # fmt: skip
        runs.append([sensors, rest, price, rng.choice(["€", "$", "kr"]), kept, steps, n])
    [ours] = run([{"op": "power", "runs": [r[:6] for r in runs]}])
    assert "runs" in ours, ours

    seen = {"cost": 0, "kept": 0, "new day": 0, "no sensor": 0}
    for (sensors, rest, price, currency, kept, steps, n), (found, saved) in zip(
        runs, ours["runs"], strict=True
    ):
        folder = tmp_path / str(n)
        folder.mkdir()
        if kept is not None:
            (folder / power.FILE).write_text(kept, encoding="utf-8")
        clock = [0.0, datetime(2026, 1, 1)]
        host = _Host()
        host.data_dir = folder
        host.log = SimpleNamespace(warning=lambda *_: None)
        options = {"sensors": sensors, "rest_w": rest, "price": price, "currency": currency}
        service = power.Power(host, options, clock=lambda c=clock: c[0], wall=lambda c=clock: c[1])
        clock[1] = datetime(*steps[0][1][:6])
        service._load()
        seen["kept"] += service.today_wh > 0
        for (now, wall, readings), got in zip(steps, found, strict=True):
            clock[0], clock[1] = now, datetime(*wall[:6])
            before = service.day
            host.published.clear()
            service.on_sample(SimpleNamespace(value=readings.get))
            rows = [list(row) for row in host.published.values()]
            for row in rows:
                if isinstance(row[1], float):
                    row[1] = {"float": repr(row[1])}
            assert got == rows, (sensors, rest, price, kept, steps, now)
            seen["cost"] += len(rows) == 5
            seen["new day"] += before != service.day
            seen["no sensor"] += rows[0][1] is None
        service._save()
        assert saved == (folder / power.FILE).read_text(encoding="utf-8"), (kept, steps)
    assert all(count > 20 for count in seen.values()), seen


def _plain_rows(rows: Any) -> list[list[Any]]:
    return [[key, {"float": repr(value)} if isinstance(value, float) else value]
            for key, value in rows]  # fmt: skip


def _floated(value: Any) -> Any:
    return {"float": repr(float(value))} if value is not None else None


def _agent_event(rng: random.Random) -> dict[str, Any]:
    from libre_panel import agents

    events = [
        *agents.WORKING,
        *agents.IDLE,
        *agents.HOOK_EVENTS,
        "",
        None,
        5,
        "Weird" * 12,
        *["PermissionRequest"] * 6,
    ]
    notices = [*agents.WAITING_NOTICES, "idle_prompt", "auth_success", None, 3]
    data: dict[str, Any] = {"hook_event_name": rng.choice(events)}
    if rng.random() < 0.9:
        data["session_id"] = rng.choice(["a", "b", "c", "d", "", None, 7, "ä" * 40])
    if rng.random() < 0.6:
        data["tool_name"] = rng.choice(["Bash", "", None, 12, "Edit" * 20, ["x"]])
    if rng.random() < 0.6:
        data["session_title"] = rng.choice([" Fix   the\tbug\n now ", "", "   ", None, 5,
                                            "x" * 200, "Größe ändern"])  # fmt: skip
    if rng.random() < 0.7:
        data["notification_type"] = rng.choice(notices)
    return data


def _agent_status(rng: random.Random, now: float) -> Any:
    from datetime import UTC, datetime

    def moment() -> Any:
        later = now + rng.choice([-50, 600, 18000, 600000])
        iso = datetime.fromtimestamp(later, UTC).strftime("%Y-%m-%dT%H:%M:%S")
        return rng.choice([later, int(later), later * 1000, str(later), iso + "Z", iso,
                           iso + ".25+02:00", "soon", "", None, 0, True])  # fmt: skip

    def limit() -> Any:
        if rng.random() < 0.15:
            return rng.choice([None, 5, []])
        return {"used_percentage": rng.choice([12, 55.5, "80", 100, 101, -1, None, True,
                                               float("nan")]),
                "resets_at": moment()}  # fmt: skip

    if rng.random() < 0.05:
        return rng.choice([None, [1], "x"])
    data: dict[str, Any] = {
        "session_id": rng.choice(["a", "b", "c", "", None]),
        "model": rng.choice([{"display_name": "Opus"}, {"display_name": "M" * 60}, {}, None,
                             {"display_name": 5}, {"display_name": ""}, "Opus"]),
        "context_window": rng.choice([{"used_percentage": rng.choice([0, 41.5, 99.5, 150,
                                                                       "33", None])}, {}, 7]),
        "cost": rng.choice([{"total_cost_usd": rng.choice([0, 0.25, 1.5, 3, "2.75", -1, None,
                                                            True, float("inf")])}, {}, None]),
    }  # fmt: skip
    if rng.random() < 0.7:
        data["rate_limits"] = {"five_hour": limit(), "seven_day": limit()}
    return data


def test_the_agents_board_tells_what_agents_py_tells(tmp_path: Any, monkeypatch: Any) -> None:
    """libre_panel/agents.py and services/agents.py: hooks, status lines and the plan's usage
    at moments of a clock and the readings, lines and notices they give; what the programs'
    answers hold; the settings with and without Libre Panel's hooks; the files and the key."""
    import os
    import re
    import sys
    from types import SimpleNamespace

    from libre_panel import agents
    from libre_panel.services import agents as service

    rng = random.Random(0xA6E7)
    text = lambda value: json.dumps(value)  # noqa: E731
    boards = []
    for _ in range(250):
        now, steps = 1.78e9 + rng.uniform(0, 3e7), []
        for _ in range(rng.randrange(1, 70)):
            now += rng.choice([0.5, 0.5, 2, 2, 30, 30, 30, 600, 1000, 7300, 22000, 90000])
            agent = rng.choice(["claude", "claude", "codex", "codex", "gemini"])
            real = rng.choice(["claude", "codex"])
            what = rng.choice(
                ["hook"] * 8
                + ["status"] * 5
                + ["look"] * 4
                + ["readings"] * 3
                + ["quota", "tokens", "failed", "usage", "heard", "state"]
            )
            if what == "hook":
                data = _agent_event(rng) if rng.random() < 0.97 else rng.choice([None, [], "x"])
                steps.append([now, what, agent, text(data)])
            elif what == "status":
                steps.append([now, what, agent, text(_agent_status(rng, now))])
            elif what == "quota":
                resets = rng.choice([None, now + 900, now - 5, 0.0])
                steps.append([now, what, real, rng.choice(["five_hour", "week"]),
                              rng.choice([0.0, 37.5, 100.0]), resets,
                              rng.choice(["background", ""])])  # fmt: skip
            elif what == "tokens":
                steps.append([now, what, real, rng.choice([None, 0.0, 125000.0]),
                              rng.choice([None, 9e6])])  # fmt: skip
            elif what == "failed":
                steps.append([now, what, real, "no answer", rng.choice(["", "old"]),
                              rng.choice(["", "2.1.0"])])  # fmt: skip
            elif what == "look":
                steps.append([now, what, rng.random() < 0.7, rng.random() < 0.8])
            else:
                steps.append([now, what, real])
        boards.append(steps)

    def reset() -> Any:
        return rng.choice([1.79e9, 1.79e12, "2026-08-01T10:00:00Z", None, "x"])

    def window(used: str, minutes: bool) -> Any:
        if rng.random() < 0.05:
            return rng.choice([None, 4])
        found = {used: rng.choice([5, 62.5, 62.5, 99, "40", "40", 120, None]),
                 "resets_at" if not minutes else "resetsAt": reset()}  # fmt: skip
        if minutes:
            found["windowDurationMins"] = rng.choice(
                [300, 360, 361, 1440, 10080, 10080, 10080, None, "300"]
            )
        return found

    answers = []
    junk = ["not json", "[1]", '"x"', "{}", '{"type": "system", "subtype": "init"}',
            '{"type": "assistant"}', '{"type": null}']  # fmt: skip
    for _ in range(400):
        lines = [junk[4] for _ in range(rng.randrange(3))]
        if rng.random() < 0.5:
            limits = {"five_hour": window("utilization", False),
                      "seven_day": window("utilization", False)}  # fmt: skip
            data: Any = {"rate_limits_available": rng.choice([True] * 6 + [False, None, 1]),
                         "rate_limits": rng.choice([limits] * 6 + [{}, None])}  # fmt: skip
            response = {"request_id": rng.choice(["libre-panel-usage"] * 12 + ["other", None]),
                        "subtype": rng.choice(["success"] * 12 + ["error"]),
                        "response": rng.choice([data] * 12 + [None, [data]])}  # fmt: skip
            message: Any = {"type": "control_response",
                            "response": rng.choice([response] * 12 + [None])}  # fmt: skip
            if rng.random() < 0.1:
                lines.insert(rng.randrange(len(lines) + 1), rng.choice(junk))
            lines.append(text(message))
            answers.append(["claude", [line + "\n" for line in lines]])
        else:
            limits = {"primary": window("usedPercent", True),
                      "secondary": window("usedPercent", True)}  # fmt: skip
            result = rng.choice([{"rateLimits": limits}, {"rateLimits": limits},
                                 {"rateLimitsByLimitId": {"codex": limits}},
                                 {"rateLimits": {}, "rateLimitsByLimitId": {"codex": limits}},
                                 {"rateLimits": None, "rateLimitsByLimitId": 5}, {}])  # fmt: skip
            buckets = [rng.choice([{"startDate": rng.choice(["2026-11-10", "2026-11-10",
                                                             "2026-11-09",
                                                             "2026-11-08", "2026-11-11", None, 5]),
                                    "tokens": rng.choice([1000, 2500.5, "70", None, True])}] * 5
                                  + [None, "x"]) for _ in range(rng.randrange(8))]  # fmt: skip
            used = rng.choice([{"dailyUsageBuckets": buckets}] * 8
                              + [{"dailyUsageBuckets": None}, {}, None, [1]])  # fmt: skip
            lines += [text({"id": 1, "result": {}}), rng.choice(junk[:4]),
                      text(rng.choice([{"id": 2, "result": result}] * 8
                                      + [{"id": 2, "error": {"message": "no"}}, {"id": "2"}])),
                      text(rng.choice([{"id": 3, "result": used}] * 5
                                      + [{"id": 3.0, "result": used}, {"id": True, "result": used},
                                         {"method": "x"}]))]  # fmt: skip
            answers.append(["codex", [line + "\n" for line in lines], "2026-11-10",
                            rng.choice(["2026-11-09", "2026-11-10"])])  # fmt: skip

    versions = ["2.1.293 (Claude Code)", " \t2.1.5\n", "v2.1.3", "2.1", "10.20.30abc", "",
                "٢.١.٣", "2 .1.3", "2.1.292", "2.2.0", "3.0.0 x", "2.1.293.4", "1..2"]  # fmt: skip
    environment = [["PATH", "/bin"], ["DISABLE_TELEMETRY", "0"], ["disable_autoupdater", "x"],
                   ["HOME", "/h"], ["Claude_Code_Disable_Nonessential_Traffic", "1"],
                   ["CLAUDE_CODE_ENABLE_TELEMETRY", "1"], ["LANG", "de_DE.UTF-8"]]  # fmt: skip

    def group(ours: bool) -> Any:
        port = rng.choice([8765, 9000, 1])
        command = (agents.hook_command(port, "claude", "s3") if ours
                   else rng.choice(["echo hi", "say /api/ai", 5]))  # fmt: skip
        return rng.choice([{"hooks": [{"type": "command", "command": command}]},
                           {"matcher": "Bash", "hooks": [{"type": "command", "command": "ls"},
                                                         {"type": "command", "command": command}]},
                           {"hooks": command}, {"hooks": [command]}, 5])  # fmt: skip

    settings = []
    for _ in range(400):
        given: dict[str, Any] = {}
        if rng.random() < 0.3:
            given["model"] = rng.choice(["opus", 1.0, {"ä": [1, None]}])
        kind = rng.random()
        if kind < 0.1:
            given["hooks"] = rng.choice([None, 5, [], "x"])
        elif kind < 0.9:
            hooks: dict[str, Any] = {}
            for event in rng.sample([*agents.HOOK_EVENTS, "Custom"], rng.randrange(5)):
                groups = [group(rng.random() < 0.5) for _ in range(rng.randrange(4))]
                hooks[event] = rng.choice([{}, 5, None]) if rng.random() < 0.05 else groups
            given["hooks"] = hooks
        if rng.random() < 0.6:
            given["statusLine"] = rng.choice([
                {"type": "command", "command": agents.status_command(8765, "s3")},
                {"type": "command", "command": "my-line"}, "", None, {},
                {"command": 5}])  # fmt: skip
        events = rng.sample([*agents.HOOK_EVENTS], rng.randrange(1, 5))
        settings.append([text(given), rng.choice([8765, 9001]), rng.choice(["s3", "k-_9"]),
                         rng.random() < 0.6, events,
                         rng.choice(["x /api/ai/hook", "echo"])])  # fmt: skip

    contents = [None, "", "  \n", "{}", '{"a": 1, "ä": "ö"}', "[1]", "{broken", '"x"',
                '{"hooks": {"Stop": []}}']  # fmt: skip
    files = []
    for n in range(60):
        content, copy = contents[n % len(contents)], (None, "old copy")[n // len(contents) % 2]
        data = {"n": n, "name": "Grüße", "list": [1, 2.5, None], "t": {"x": True}}
        for side in ("ours", "theirs"):
            folder = tmp_path / side / str(n) / "sub"
            if content is not None or copy is not None:
                folder.mkdir(parents=True)
            if content is not None:
                (folder / "settings.json").write_text(content, encoding="utf-8")
            if copy is not None:
                (folder / "settings.json.libre-panel.bak").write_text(copy, encoding="utf-8")
        files.append([content, copy, data])
    keys = ['{"key": "abc"}', '{"key": 5}', "{broken", "[]", None, '{"key": ""}']
    for n, content in enumerate(keys):
        folder = tmp_path / "keys" / str(n)
        if content is not None:
            folder.mkdir(parents=True)
            (folder / agents.KEY_FILE).write_text(content, encoding="utf-8")

    [ours] = run([{
        "op": "agents",
        "boards": boards,
        "answers": answers,
        "versions": versions,
        "environment": environment,
        "settings": [[s[0], str(s[1]), *s[2:]] for s in settings],
        "files": [[str(tmp_path / "ours" / str(n) / "sub" / "settings.json"), text(f[2])]
                  for n, f in enumerate(files)],
        "keys": [str(tmp_path / "keys" / str(n)) for n in range(len(keys))],
    }])  # fmt: skip
    assert "boards" in ours, ours

    seen = {"waiting": 0, "line": 0, "quota": 0, "cost": 0, "listed": 0, "hidden": 0, "note": 0}
    for steps, found in zip(boards, ours["boards"], strict=True):
        clock = [0.0]
        board = agents.Board(clock=lambda c=clock: c[0])
        host = SimpleNamespace(published={}, said=[], log=None)
        host.publish = lambda key, value, host=host: host.published.__setitem__(key, value)
        host.notify = lambda text, host=host, **how: host.said.append([text, how])
        shown: dict[tuple[bool, bool], Any] = {}
        states: dict[str, str] = {}
        for step, got in zip(steps, found, strict=True):
            (clock[0], what), rest = step[:2], step[2:]
            if what == "hook":
                want: Any = board.hook(rest[0], json.loads(rest[1]))
            elif what == "status":
                want = board.status(rest[0], json.loads(rest[1]))
                seen["line"] += " · " in want
            elif what == "quota":
                want = board.quota(rest[0], rest[1], rest[2], rest[3], source=rest[4])
            elif what == "tokens":
                want = board.tokens(*rest)
            elif what == "failed":
                want = board.usage_failed(*rest)
            elif what == "readings":
                readings = board.readings()
                want = _plain_rows(readings.items())
                seen["quota"] += readings["ai.claude.five_hour"] is not None
                seen["cost"] += bool(readings["ai.claude.cost_today"])
                seen["listed"] += readings["ai.claude.s2.state"] is not None
            elif what == "usage":
                usage = board.usage(rest[0])
                want = [usage["source"], _floated(usage["ago"]), usage["error"],
                        usage["reason"], usage["version"]]  # fmt: skip
            elif what == "heard":
                ago, word = board.last_heard(rest[0])
                want = [_floated(ago), word]
            elif what == "state":
                state, tool, since = board.state(rest[0])
                want = [state, tool, _floated(since)]
            else:
                named, notice = rest
                options = {"claude_usage": True, "codex_usage": True, "task": named,
                           "notice": notice}  # fmt: skip
                looking = shown.setdefault((named, notice), service.Agents(host, options, board))
                looking._states = states  # (one service whose options change)
                host.published.clear()
                host.said.clear()
                looking.publish()
                assert got["published"] == _plain_rows(host.published.items()), steps
                said = []
                for agent, task in got["waiting"]:
                    name = service.AI_NAMES[agent]
                    said.append([task or "Waiting for you",
                                 {"icon": "agent", "level": "info", "seconds": 6.0,
                                  "kind": "agent", "rank": 2, "key": f"ai-{agent}",
                                  "payload": {"label": name + " · Waiting for you"}}])  # fmt: skip
                assert said == host.said, steps
                seen["waiting"] += len(said)
                seen["hidden"] += not named and got["published"][3][1] is None
                seen["note"] += len(got["notes"])
                continue
            assert got == want, (step, steps)
    assert all(count > 20 for count in seen.values()), seen

    seen = {"ok": 0, "old": 0, "tokens": 0, "week": 0}
    asked: list[Any] = []
    for (who, lines, *days), got in zip(answers, ours["answers"], strict=True):
        try:
            if who == "claude":
                quotas = agents.claude_quotas(agents.claude_usage_reply(lines))
                want = {"ok": [[w, _floated(u), _floated(r)] for w, (u, r) in quotas.items()]}
            else:
                monkeypatch.setattr(agents, "_exchange",
                                    lambda command, requests, *_, lines=lines, **__:
                                    asked.append([command, requests]) or lines)  # fmt: skip
                quotas, used = agents.ask_codex("codex")
                tokens = agents.codex_tokens(used, *days) if used is not None else None
                want = {"ok": [[w, _floated(u), _floated(r)] for w, (u, r) in quotas.items()],
                        "tokens": tokens and [_floated(t) for t in tokens]}  # fmt: skip
                seen["tokens"] += bool(tokens and tokens[0])
            seen["ok"] += len(want["ok"]) == 2
            seen["week"] += any(w == "week" for w, *_ in want["ok"])
        except ValueError as exc:
            want = {"err": [str(exc), getattr(exc, "reason", ""), getattr(exc, "version", "")]}
            seen["old"] += bool(want["err"][1])
        assert got == want, lines
    assert all(count > 20 for count in seen.values()), seen

    for said, got in zip(versions, ours["versions"], strict=True):
        found = re.match(r"\s*(\d+)\.(\d+)\.(\d+)", said)
        assert got == (found and [int(part) for part in found.groups()]), said
    monkeypatch.setattr(agents.os, "environ", dict(environment))
    assert ours["environment"] == [list(pair) for pair in agents.claude_environment().items()]
    assert ours["requests"] == [json.dumps(agents.CLAUDE_REQUEST), asked[0][1]]
    assert asked[0][0] == ["codex", "app-server"]
    assert ours["commands"] == [agents.hook_command(8765, "codex", "k"),
                                agents.status_command(1, "s")]  # fmt: skip

    def made(make: Any) -> dict[str, str]:
        try:
            return {"ok": json.dumps(make())}
        except ValueError as exc:
            return {"err": str(exc)}

    seen = {"err": 0, "ours": 0, "line": 0, "other": 0, "port": 0}
    for (given, port, secret, line, events, command), got in zip(
        settings, ours["settings"], strict=True
    ):
        given = json.loads(given)
        there = agents.installed(given)
        want = {
            "with": made(lambda g=given, e=events, c=command: agents.with_hooks(g, tuple(e), c)),
            "without": made(lambda g=given: agents.without_ours(g)),
            "installed": [there["hooks"], there["status_line"], there["other_status_line"],
                          there["port"]],
            "claude": made(lambda g=given, p=port, s=secret, n=line:
                           agents.claude_plan(g, p, s, n)),
            "codex": made(lambda g=given, p=port, s=secret: agents.codex_plan(g, p, s)),
        }  # fmt: skip
        if got["installed"][3] is not None:
            got["installed"][3] = int(got["installed"][3])
        assert got == want, given
        seen["err"] += "err" in want["with"]
        seen["ours"] += there["hooks"]
        seen["line"] += there["status_line"]
        seen["other"] += there["other_status_line"]
        seen["port"] += there["port"] is not None
    assert all(count > 20 for count in seen.values()), seen

    seen = {"new": 0, "copy": 0, "kept": 0, "bad": 0}
    for n, ((content, copy, data), (kept, copied)) in enumerate(
        zip(files, ours["files"], strict=True)
    ):
        mine = tmp_path / "ours" / str(n) / "sub" / "settings.json"
        path = tmp_path / "theirs" / str(n) / "sub" / "settings.json"
        try:
            read = agents.read_settings(path)
        except ValueError as exc:
            assert "err" in kept, content
            if "holds no" in str(exc):
                assert kept["err"] == str(exc)
            assert copied is None
            seen["bad"] += 1
            continue
        assert kept == {"ok": json.dumps(read)}, content
        backup = agents.write_settings(path, data)
        assert (copied and copied[len(str(mine.parent)) :]) == (
            backup and str(backup)[len(str(path.parent)) :]
        )
        assert mine.read_bytes() == path.read_bytes()
        if backup is not None:
            assert mine.with_name(backup.name).read_bytes() == backup.read_bytes()
            seen["copy" if copy is None else "kept"] += 1
        else:
            seen["new"] += 1
        assert sorted(p.name for p in mine.parent.iterdir()) == sorted(
            p.name for p in path.parent.iterdir()
        )
    assert all(count > 3 for count in seen.values()), seen

    for n, ((read, new, again), content) in enumerate(zip(ours["keys"], keys, strict=True)):
        folder = tmp_path / "keys" / str(n)
        monkeypatch.setattr(agents, "key_file", lambda folder=folder: folder / agents.KEY_FILE)
        assert again == new == agents.read_key()
        if content == keys[0]:
            assert read == new == "abc"
            continue
        assert read == "" and re.fullmatch(r"[A-Za-z0-9_-]{32}", new), new
        if sys.platform != "win32":
            assert os.stat(folder / agents.KEY_FILE).st_mode & 0o777 == 0o600
        assert [p.name for p in folder.iterdir()] == [agents.KEY_FILE]
    assert len({new for _, new, _ in ours["keys"]}) == len(keys)


def test_notices_come_when_notices_py_sends_them() -> None:
    """services/notices.py: the volume, a new track and its cover, the microphone, an
    appointment soon, sensors past their limits (the quiet time, a worse level), the alarm
    and its hold, the panel back from a restart; what is wrong with the options and the
    readings it asks for."""
    from datetime import datetime
    from types import SimpleNamespace

    from libre_panel import i18n
    from libre_panel.sensors.base import Reading, Snapshot
    from libre_panel.services import notices

    limits = [{"key": "cpu.temp", "warn": 70, "crit": 90},
              {"key": "gpu.load", "warn": 80, "max": 200},
              {"key": "x.y", "crit": 50, "name": "Pump", "icon": "fan"},
              {"key": " disk.used ", "crit": 95.5, "icon": 5, "name": 7},
              {"key": "cpu.temp", "crit": 93}, {"key": ""}, 5, {"key": "a", "warn": "x"},
              {"key": "a"}, {"key": "a", "max": True},
              {"key": "a", "warn": 9, "crit": 3},
              {"key": "fan.1", "warn": 2000, "icon": ""}]  # fmt: skip

    def readings_of(rng: random.Random) -> dict[str, list[Any]]:
        pool: dict[str, tuple[list[Any], str, list[str]]] = {
            "audio.volume": ([30, 30.5, 31, 55.0, 55.4, 80, None, "x", True], "%", ["Volume"]),
            "audio.muted": ([0, 0, 1, None], "", ["Muted"]),
            "audio.mic.muted": ([0, 1, 1, 0.0, None, "1"], "", [""]),
            "media.title": (["Song A", "Song A", "Song B", "Größe", None, 5], "", [""]),
            "media.artist": (["Band", "Band", "", None], "", [""]),
            "media.state": (["Playing", "playing", "Playing", "Paused", None], "", [""]),
            "media.playing": ([1, 0, None], "", [""]),
            "media.progress": ([0.25, 0.9, None, "x"], "", [""]),
            "cpu.temp": ([60, 75, 75.25, 92, 95, None], "°C", ["CPU temperature", ""]),
            "gpu.load": ([50, 85, 210, 9.96], "%", ["GPU load"]),
            "x.y": ([40, 55, 4.25, 50], "l/h", ["Flow", ""]),
            "disk.used": ([96, 9.5, 95.5], "%", ["Disk"]),
            "fan.1": ([1500, 2400], "RPM", ["Fan 1"]),
        }
        for n in (1, 2, 3):
            pool[f"calendar.{n}.title"] = (["Standup", "Lunch", "Lunch", None, 5], "", [""])
            pool[f"calendar.{n}.minutes"] = ([5, 0.2, 0.5, 0.49, 12, -1, 2.5, 1.5, None], "", [""])
            pool[f"calendar.{n}.start"] = ([1.78e9, 1.78e9 + 600, 1780000300, None], "", [""])
            pool[f"calendar.{n}.time"] = (["09:30", "09:30", i18n.t("All day"), "", None], "", [""])
        return {key: [rng.choice(values), unit, rng.choice(labels)]
                for key, (values, unit, labels) in pool.items() if rng.random() < 0.75}  # fmt: skip

    defaults = {name: default for name, (_, default) in notices.Notices.options.items()}
    for language in ("en", "de"):
        i18n.set_language(language)
        try:
            rng = random.Random(0x4071CE)
            runs = []
            for _ in range(250):
                options = {**defaults, "warnings": rng.sample(limits, rng.randrange(5)),
                           "volume_look": rng.choice(["", "", "ring", "nope"]),
                           "appointments": rng.choice([10, 10, 0, 30]),
                           "quiet_s": rng.choice([120.0, 0, 5])}  # fmt: skip
                for name in ("volume", "music", "microphone", "restarted", "alarm"):
                    options[name] = rng.random() < 0.85
                began = rng.uniform(0, 1000)
                now, steps = began, []
                for _ in range(rng.randrange(1, 60)):
                    now += rng.choice([0.05, 0.3, 1, 1, 1, 2.5, 5, 12, 40, 130])
                    kind = rng.choice(["sample"] * 7 + ["fresh"] * 2 + ["restarted"])
                    pictures = [key for key in (notices.COVER, notices.APP_ICON)
                                if rng.random() < 0.4]  # fmt: skip
                    more: Any = "14:05"
                    if kind == "fresh":
                        more = rng.sample(["audio.volume", "audio.muted", "media.title",
                                           "media.state", "audio.mic.muted", "cpu.temp"],
                                          rng.randrange(1, 4))  # fmt: skip
                    steps.append([now, kind, json.dumps(readings_of(rng)), pictures, more])
                runs.append([json.dumps(options), began, steps])
            [ours] = run([{"op": "notices", "locale": str(i18n.LOCALE_DIR), "language": language,
                           "runs": runs}])  # fmt: skip
            assert "runs" in ours, ours

            seen = {kind: 0 for kind in ("volume", "music", "microphone", "appointment",
                                         "warning", "panel", "publish", "unpublish", "note",
                                         "late cover", "error", "report")}  # fmt: skip
            for (options, began, steps), got in zip(runs, ours["runs"], strict=True):
                clock = [began]
                acts: list[list[Any]] = []
                reports: list[str] = []
                heard: dict[str, Any] = {}
                wants: list[str] = []
                log = SimpleNamespace(
                    warning=lambda _, text, reports=reports: reports.append(text),
                    info=lambda text, *more, acts=acts: acts.append(["note", text % more]),
                    debug=lambda *_: None,
                )

                def publish(key: str, value: Any, unit: str, label: str, acts: Any = acts) -> None:
                    assert (unit, label) == ("", i18n.t("Alarm"))
                    acts.append(["publish", key, value])

                def notify(text: str, acts: Any = acts, **how: Any) -> None:
                    acts.append(["tell", text, how["icon"], how["level"], how["seconds"],
                                 how["kind"], how["rank"], how["key"],
                                 json.dumps(how["payload"])])  # fmt: skip

                host = SimpleNamespace(
                    log=log, publish=publish, notify=notify,
                    want=lambda *keys, wants=wants: wants.extend(keys),
                    on=lambda event, callback, heard=heard: heard.__setitem__(event, callback),
                    unpublish=lambda key, acts=acts: acts.append(["unpublish", key]),
                )  # fmt: skip
                service = notices.Notices(host, json.loads(options), clock=lambda c=clock: c[0],
                                          wall=lambda: datetime(2026, 1, 1, 14, 5))  # fmt: skip
                service.start()
                assert got["reports"] == reports, options
                assert got["wants"] == wants, options
                assert got["follows"] == ("fresh" in heard), options
                assert got["alarms"] == (acts == [["publish", "alarm", 0]]), options
                seen["report"] += len(reports)
                for (now, kind, readings, pictures, more), found in zip(
                    steps, got["steps"], strict=True
                ):
                    clock[0] = now
                    acts.clear()
                    late = service._late is not None
                    snapshot = Snapshot(
                        readings={key: Reading(key, *row)
                                  for key, row in json.loads(readings).items()},
                        images={key: key for key in pictures},
                    )  # fmt: skip
                    if kind == "sample":
                        service.on_sample(snapshot)
                    elif kind == "fresh":
                        if "fresh" in heard:
                            service.on_fresh(snapshot, frozenset(more))
                    else:
                        service.on_restarted()
                    if kind == "fresh" and "fresh" not in heard:
                        continue  # (not listened for: the loop does not tell it)
                    assert found == acts, (options, steps, now)
                    for act in acts:
                        seen[act[5] if act[0] == "tell" else act[0]] += 1
                        seen["error"] += act[0] == "tell" and act[3] == "error"
                        seen["late cover"] += late and act[0] == "tell" and act[5] == "music"
            assert all(count > 20 for count in seen.values()), seen
        finally:
            i18n.set_language("en")
