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
