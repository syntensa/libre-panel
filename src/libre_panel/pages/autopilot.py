"""The Autopilot page: the rules of the ``autopilot`` service, and what it is doing.

The rules are ``[[services.autopilot.rules]]`` in config.toml (docs/CONFIGURATION.md);
the page reads and writes them there, so the file stays the one place they live, and the
service restarts by itself when it changes. What the service is doing now (its rule, its
theme, the signals it looks at) comes from its ``status()``.
"""

from __future__ import annotations

from typing import Any

from libre_panel.config import Config, ConfigError, edit_config, load_config
from libre_panel.i18n import t
from libre_panel.pages.base import ConsolePage, Refused, Request
from libre_panel.pages.control import service_state, with_service
from libre_panel.plugins.loader import registry
from libre_panel.render.overlays import BUILT_IN_TRANSITIONS
from libre_panel.services import autopilot as service
from libre_panel.theme.model import list_themes

SERVICE = "autopilot"
RULES = f"services.{SERVICE}.rules"
LOADS = ("cpu_over", "gpu_over")
LONGEST_S = 86400  # no setting of the autopilot is longer than a day


def is_number(value: Any) -> bool:
    return isinstance(value, int | float) and not isinstance(value, bool)


def condition_problem(key: str, value: Any) -> str:
    """What is wrong with one condition of a rule, in words; "" when nothing is."""
    if key in ("music", "microphone"):
        return "" if isinstance(value, bool) else t("{key} is on or off", key=key)
    if key in LOADS:
        ok = is_number(value) and 0 <= value <= 100
        return "" if ok else t("{key} is a load between 0 and 100", key=key)
    if key == "for_s":
        return "" if is_number(value) and value > 0 else t("for_s is a number of seconds")
    if key == "apps":
        names = value if isinstance(value, list) else []
        ok = bool(names) and all(isinstance(n, str) and n.strip() for n in names)
        return "" if ok else t("apps is a list of program names")
    if key == "time":
        ok = isinstance(value, str) and service.parse_time(value) is not None
        return "" if ok else t('time looks like "22:00-06:30"')
    return t("unknown condition {key}", key=key)


def rule_problem(rule: Any) -> str:
    """What is wrong with a rule, in words; "" when it is fine."""
    if not isinstance(rule, dict):
        return t("a rule is a table")
    if not isinstance(rule.get("name"), str) or not rule["name"].strip():
        return t("every rule needs a name")
    if not isinstance(rule.get("theme"), str) or not rule["theme"]:
        return t("every rule needs a theme")
    when = rule.get("when")
    if not isinstance(when, dict) or not when:
        return t("a rule needs at least one condition")
    for key, value in when.items():
        if problem := condition_problem(key, value):
            return problem
    for key in ("enabled", "immediate"):
        if key in rule and not isinstance(rule[key], bool):
            return t("{key} is on or off", key=key)
    if "hold_s" in rule and not (is_number(rule["hold_s"]) and 0 <= rule["hold_s"] <= LONGEST_S):
        return t("hold_s is a number of seconds")
    return service.parse_conditions(when)[1]  # the service's own word, if it finds more


def settings_problem(options: Any) -> str:
    if not isinstance(options, dict):
        return t("options is a table")
    for key, (kind, _) in service.Autopilot.options.items():
        if key == "rules" or key not in options:
            continue
        value = options[key]
        if kind == "string":
            if not isinstance(value, str):
                return t("{key} is a name", key=key)
        elif not (is_number(value) and 0 <= value <= LONGEST_S):
            return t("{key} is a number of seconds", key=key)
    return ""


class Autopilot(ConsolePage):
    title = {"en": "Autopilot", "de": "Autopilot"}
    routes = {
        ("GET", "settings"): "read",
        ("GET", "status"): "status",
        ("POST", "settings"): "save",
    }

    def _config(self) -> Config:
        try:
            return load_config(self.context.config_path)
        except ConfigError as exc:
            raise Refused(str(exc), 409) from exc

    def status(self, request: Request) -> dict[str, Any]:
        """The service now: whether it is on and runs, and what it is doing."""
        try:
            config = load_config(self.context.config_path)
        except ConfigError:
            config = None  # the manager's word is all there is
        return self._status(config)

    def _status(self, config: Config | None) -> dict[str, Any]:
        context = self.context
        enabled = SERVICE in config.services.enabled if config else None
        if context.background:
            code, detail = service_state(context.services().get(SERVICE), enabled)
        else:
            code, detail = "unknown", ""  # this editor does not run services
        running = context.service(SERVICE)
        return {
            "background": context.background,
            "enabled": enabled,
            "state": code,
            "detail": detail,
            "status": running.status() if running is not None else None,
        }

    def read(self, request: Request) -> dict[str, Any]:
        config = self._config()
        given = config.services.options.get(SERVICE, {})
        rules = given.get("rules", [])
        rules = rules if isinstance(rules, list) else []
        themes = [theme["id"] for theme in list_themes()]
        problems = []
        for rule in rules:
            problem = rule_problem(rule)
            if not problem and rule.get("theme") not in themes:
                theme = rule.get("theme")
                problem = t(
                    "theme {theme} is not installed: the autopilot skips this rule", theme=theme
                )
            problems.append(problem)
        return {
            **self._status(config),
            "options": {
                key: given.get(key, default)
                for key, (_, default) in service.Autopilot.options.items()
                if key != "rules"
            },
            "rules": rules,
            "problems": problems,
            "themes": themes,
            "transitions": ["", *BUILT_IN_TRANSITIONS, *registry().names("transitions")],
        }

    def save(self, request: Request) -> dict[str, Any]:
        body = request.body
        rules, options, enabled = body.get("rules"), body.get("options"), body.get("enabled")
        if rules is not None:
            if not isinstance(rules, list):
                raise Refused(t("rules is a list"))
            for number, rule in enumerate(rules, 1):
                if problem := rule_problem(rule):
                    name = rule.get("name") if isinstance(rule, dict) else None
                    raise Refused(t("Rule {name}: {problem}", name=name or number, problem=problem))
        if options is not None and (problem := settings_problem(options)):
            raise Refused(problem)
        if enabled is not None and not isinstance(enabled, bool):
            raise Refused(t("enabled is on or off"))

        config = self._config()
        given = config.services.options.get(SERVICE, {})
        changes: list[tuple[str, str, Any]] = []
        for key, value in (options or {}).items():  # only what differs from what is in effect
            if key in service.Autopilot.options and key != "rules":
                if value != given.get(key, service.Autopilot.options[key][1]):
                    changes.append(("value", f"services.{SERVICE}.{key}", value))
        if enabled is not None and enabled != (SERVICE in config.services.enabled):
            names = with_service(config.services.enabled, SERVICE, enabled)
            changes.append(("value", "services.enabled", names))
        if rules is not None and rules != given.get("rules", []):  # else their comments stay
            changes.append(("tables", RULES, rules))
        try:
            if changes:
                edit_config(*changes, path=self.context.config_path)  # one write: one restart
        except (ConfigError, OSError, ValueError) as exc:
            raise Refused(str(exc), 409) from exc
        except TypeError as exc:  # a value config.toml cannot hold (a rule's own extra key)
            raise Refused(str(exc)) from exc
        if changes:
            self.context.follow_config()
        return self.read(request)
