"""One click for what Linux needs an administrator for: the panel's USB permission, the
mainboard's fan chip, the processor's power and the SATA drives' temperatures.

Libre Panel reads most sensors with no rights at all (``sensors/linux.py``). A few things need
a change of the system that only root can make, once:

- **the panel** (``udev``): a rule that lets the person at the computer use the TURZX USB panels
  (the rule of ``assets/60-libre-panel.rules``), applied to a panel that is plugged in;
- **the mainboard's fans and temperatures** (``modules``): the kernel has drivers for the
  Super I/O chips (nct6775, it87, nct6683) that are not loaded by themselves. The setup tries the
  ones that fit the board (by what the firmware says it is) and keeps the ones that find a chip;
  it never forces a driver (no parameter, no "ignore the firmware's claim"), and what
  ``sensors-detect`` has recorded counts as a hint;
- **the SATA drives' temperatures** (``drives``): the ``drivetemp`` driver;
- **the processor's power** (``rapl``), only if asked for: the kernel has let only root read the
  energy counters since 2020, because they leak what the processor computes (CVE-2020-8694,
  "PLATYPUS"). A rule opens them for every user; this is off unless the person says so.

``plan`` says what has to be done on this system, from facts that ``gather`` collects (and a test
makes up); ``apply`` does it through a ``System`` and reports each step. Nothing is written when
it is there already, nothing else is touched, ``Options(remove=True)`` takes it all out again.
The setup runs as root through pkexec (polkit's own password dialog; the app, the AppImage and a
terminal all start it that way), or through sudo in a terminal.
"""

from __future__ import annotations

import dataclasses
import json
import logging
import os
import re
import shlex
import shutil
import subprocess
import sys
import tempfile
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol

from libre_panel.i18n import t

log = logging.getLogger(__name__)

USB_VENDOR = "1cbe"  # TURZX USB panels, the ids the udev rule is for
UDEV_RULES = "/etc/udev/rules.d/60-libre-panel.rules"
RAPL_RULES = "/etc/udev/rules.d/60-libre-panel-rapl.rules"
MODULES = "/etc/modules-load.d/libre-panel.conf"
OWN_FILES = (UDEV_RULES, RAPL_RULES, MODULES)
RULE_FOLDERS = (
    "/etc/udev/rules.d",
    "/run/udev/rules.d",
    "/usr/local/lib/udev/rules.d",
    "/usr/lib/udev/rules.d",
    "/lib/udev/rules.d",
)
SYSTEM_PATH = "/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"
POWERCAP = "/sys/class/powercap"
DRIVE_MODULE = "drivetemp"
# The kernel drivers for the chips on a mainboard that read fans and temperatures, tried in
# this order for the maker of the board. A driver that does not find its chip refuses to load
# (and costs nothing); none of them is given a parameter.
BOARD_DRIVERS = (
    (("asus",), ("nct6775",)),
    (("gigabyte", "aorus"), ("it87", "nct6775")),
    (("micro-star", "msi"), ("nct6775", "nct6683", "it87")),
    (("asrock",), ("nct6775", "it87", "nct6683")),
    (("biostar",), ("nct6775", "it87")),
    (("evga",), ("nct6775",)),
    (("supermicro",), ("nct6775",)),
    (("intel",), ("nct6683",)),
)
# What ``sensors-detect`` may have recorded that this setup loads for the person.
RECORDED_DRIVERS = frozenset(
    {"nct6775", "nct6683", "it87", "w83627ehf", "w83627hf", "f71882fg", "dme1737", "sch5627"}
)
KNOWN_MODULES = (*{name for _, names in BOARD_DRIVERS for name in names}, *RECORDED_DRIVERS)
# DMI chassis types of portables (notebook, handheld, tablet, ...): they have an embedded
# controller where a desktop has a Super I/O chip, and their drivers are other ones.
PORTABLE_CHASSIS = frozenset({8, 9, 10, 11, 14, 30, 31, 32})
VIRTUAL_MACHINES = ("virtual", "vmware", "qemu", "kvm", "xen", "bochs", "parallels", "bhyve")

# modprobe's answers
LOADED, ABSENT, BUSY, MISSING, FAILED = "loaded", "none", "busy", "missing", "failed"


# -- the facts a plan is made from -------------------------------------------------------------


@dataclass(frozen=True)
class Options:
    """What the person asked for. ``rapl``: True opens the energy counters, False closes them
    again, None leaves them as they are. ``remove`` takes out everything the setup installed."""

    rapl: bool | None = None
    remove: bool = False


@dataclass(frozen=True)
class Facts:
    """What this computer is. ``gather`` reads it; a test says it."""

    dmi: Mapping[str, str] = field(default_factory=dict)  # board_vendor, chassis_type, ...
    loaded: frozenset[str] = frozenset()  # kernel modules loaded now
    available: frozenset[str] = frozenset()  # modules (of KNOWN_MODULES, drivetemp) this kernel has
    hwmon: tuple[str, ...] = ()  # the names of the chips there are
    recorded: tuple[str, ...] = ()  # drivers sensors-detect recorded (RECORDED_DRIVERS only)
    disks: bool = False  # SATA drives that drivetemp could read
    rapl: tuple[str, ...] = ()  # the energy counters (their files)
    rapl_closed: tuple[str, ...] = ()  # ... of them those that other users than root may not read
    panel: bool = False  # a TURZX USB panel is plugged in
    panel_denied: bool = False  # ... which this user may not open
    rule_elsewhere: bool = False  # another udev rule already lets users use the panels
    files: Mapping[str, str | None] = field(default_factory=dict)  # the contents of OWN_FILES
    tools: Mapping[str, str] = field(default_factory=dict)  # udevadm, modprobe, chmod: their paths


def supported() -> bool:
    """Whether this system has the setup: Linux does."""
    return sys.platform.startswith("linux")


def udev_rules_text() -> str:
    """The rule for the panels, the file ``libre-panel udev-rules`` prints."""
    return (Path(__file__).resolve().parent / "assets" / "60-libre-panel.rules").read_text(
        encoding="utf-8"
    )


def rapl_rules_text(chmod: str = "/usr/bin/chmod") -> str:
    return (
        "# Libre Panel: lets every user read the processor's energy counters, for its power\n"
        "# (cpu.power). Since 2020 the kernel keeps them to root: they leak what a processor\n"
        "# computes (CVE-2020-8694, PLATYPUS). Leave this out on a PC where you do not trust\n"
        "# every program you run.\n"
        "# Written by `libre-panel system-setup --linux --rapl`, taken out by `--remove`.\n"
        'SUBSYSTEM=="powercap", ACTION=="add|change", TEST=="energy_uj", '
        f'RUN+="{chmod} a+r /sys%p/energy_uj"\n'
    )


def modules_text(names: Sequence[str]) -> str:
    return (
        "# Written by `libre-panel system-setup --linux`: drivers for the sensors of this PC.\n"
        "# `libre-panel system-setup --linux --remove` takes this file out again.\n"
        + "".join(f"{name}\n" for name in names)
    )


def persisted(content: str | None) -> list[str]:
    """The modules a modules-load.d file names."""
    lines = (content or "").splitlines()
    return [line.strip() for line in lines if line.strip() and not line.strip().startswith("#")]


# -- the plan --------------------------------------------------------------------------------


@dataclass(frozen=True)
class Write:
    path: str
    content: str
    mode: int = 0o644


@dataclass(frozen=True)
class Remove:
    path: str


@dataclass(frozen=True)
class Run:
    argv: tuple[str, ...]


@dataclass(frozen=True)
class Load:
    """Try these kernel modules, in this order (``first``: stop at the first that finds its
    chip); those that do are written into ``persist``, so they are loaded at every start."""

    modules: tuple[str, ...]
    first: bool = False
    persist: str = MODULES


Action = Write | Remove | Run | Load


@dataclass(frozen=True)
class Step:
    id: str  # udev, modules, drives, rapl, reload
    actions: tuple[Action, ...]
    modules: tuple[str, ...] = ()  # for the modules: which ones are tried


def board_drivers(facts: Facts) -> list[str]:
    """The drivers worth trying for the fans and temperatures on this board, from what the
    firmware says it is; none for a computer that is not a desktop board (a notebook, a virtual
    machine, a maker's own PC) or whose chip a driver already reads."""
    if any(kind == "board" for kind in map(_chip_kind, facts.hwmon)):
        return []
    found = [name for name in facts.recorded if name in facts.available]
    if found:  # the person ran sensors-detect: its word counts more than a guess
        return found
    dmi = {key: value.lower() for key, value in facts.dmi.items()}
    try:
        chassis = int(dmi.get("chassis_type", ""))
    except ValueError:
        chassis = 0
    machine = f"{dmi.get('sys_vendor', '')} {dmi.get('product_name', '')}"
    if chassis in PORTABLE_CHASSIS or any(word in machine for word in VIRTUAL_MACHINES):
        return []
    maker = dmi.get("board_vendor", "")
    for words, names in BOARD_DRIVERS:
        if any(word in maker for word in words):
            return [name for name in names if name in facts.available]
    return []


def _chip_kind(name: str) -> str:
    from libre_panel.sensors.hwmon import kind_of

    return kind_of(name)


def plan(facts: Facts, options: Options | None = None) -> list[Step]:
    """What has to be done to bring the person what they asked for, on this computer: only what
    is not so already (a computer that is set up has an empty plan)."""
    options = options or Options()
    return removal(facts) if options.remove else installation(facts, options)


def installation(facts: Facts, options: Options) -> list[Step]:
    steps: list[Step] = []
    changed: set[str] = set()  # the subsystems whose rules changed
    tools = facts.tools
    rules = udev_rules_text()
    ours = facts.files.get(UDEV_RULES)
    if ours != rules and not (ours is None and facts.rule_elsewhere):
        steps.append(Step("udev", (Write(UDEV_RULES, rules),)))
        changed.add("usb")
    drivers = board_drivers(facts)
    if drivers:
        steps.append(Step("modules", (Load(tuple(drivers), first=True),), tuple(drivers)))
    drive = DRIVE_MODULE
    if (
        facts.disks
        and drive in facts.available
        and drive not in facts.loaded
        and drive not in persisted(facts.files.get(MODULES))
    ):
        steps.append(Step("drives", (Load((drive,)),), (drive,)))
    if options.rapl is True and facts.rapl:
        actions: list[Action] = []
        rule = rapl_rules_text(tools.get("chmod", "/usr/bin/chmod"))
        if facts.files.get(RAPL_RULES) != rule:
            actions.append(Write(RAPL_RULES, rule))
            changed.add("powercap")
        if facts.rapl_closed:
            actions.append(Run((tools.get("chmod", "chmod"), "a+r", *facts.rapl_closed)))
        if actions:
            steps.append(Step("rapl", tuple(actions)))
    elif options.rapl is False and facts.files.get(RAPL_RULES) is not None:
        steps.append(Step("rapl", _close_rapl(facts)))
        changed.add("powercap")
    if changed:
        steps.append(_reload(facts, changed))
    return steps


def _close_rapl(facts: Facts) -> tuple[Action, ...]:
    actions: list[Action] = [Remove(RAPL_RULES)]
    opened = [path for path in facts.rapl if path not in facts.rapl_closed]
    if opened:  # back to what the kernel makes them
        actions.append(Run((facts.tools.get("chmod", "chmod"), "0400", *opened)))
    return tuple(actions)


def removal(facts: Facts) -> list[Step]:
    """Take out what the setup put in. Drivers stay loaded until the next start: another
    program (fancontrol, say) may be reading them."""
    steps: list[Step] = []
    changed: set[str] = set()
    if facts.files.get(UDEV_RULES) is not None:
        steps.append(Step("udev", (Remove(UDEV_RULES),)))
        changed.add("usb")
    if facts.files.get(MODULES) is not None:
        steps.append(Step("modules", (Remove(MODULES),)))
    if facts.files.get(RAPL_RULES) is not None:
        steps.append(Step("rapl", _close_rapl(facts)))
        changed.add("powercap")
    if changed:
        steps.append(_reload(facts, changed))
    return steps


def _reload(facts: Facts, changed: set[str]) -> Step:
    """udev reads the rules again, and a panel that is plugged in is looked at again, so that
    no one has to unplug it."""
    udevadm = facts.tools.get("udevadm", "udevadm")
    runs: list[Action] = [Run((udevadm, "control", "--reload-rules"))]
    if "usb" in changed:
        runs.append(
            Run(
                (udevadm, "trigger", "--subsystem-match=usb", f"--attr-match=idVendor={USB_VENDOR}")
            )
        )
    if "powercap" in changed:
        runs.append(Run((udevadm, "trigger", "--subsystem-match=powercap")))
    return Step("reload", tuple(runs))


# -- doing it ----------------------------------------------------------------------------------


class System(Protocol):
    """What the setup does to a computer (a test gives its own)."""

    def read(self, path: str) -> str | None: ...
    def write(self, path: str, content: str, mode: int) -> None: ...
    def remove(self, path: str) -> bool: ...
    def run(self, argv: Sequence[str]) -> tuple[int, str]: ...
    def loaded(self, module: str) -> bool: ...
    def tool(self, name: str) -> str | None: ...
    def chips(self) -> tuple[str, ...]: ...


@dataclass
class Result:
    """How one step went: ``done`` or ``failed``, and per file or module what became of it."""

    id: str
    status: str = "done"
    items: dict[str, str] = field(default_factory=dict)
    detail: str = ""


def classify(code: int, output: str) -> str:
    """What modprobe's answer means for a driver that looks for a chip."""
    if code == 0:
        return LOADED
    text = output.lower()
    if "no such device" in text:
        return ABSENT  # the chip is not on this board (the driver looked, and said so)
    if "resource busy" in text:
        return BUSY  # the firmware keeps the chip for itself (ACPI): not forced
    if "not found" in text or "no such file" in text:
        return MISSING
    return FAILED


def apply(
    steps: Sequence[Step], system: System, log_line: Callable[[str], None] = log.info
) -> list[Result]:
    """Do the steps, one after the other; a step that fails does not stop the others."""
    results = []
    for step in steps:
        result = Result(step.id)
        for action in step.actions:
            try:
                _do(action, system, result, log_line)
            except Exception as exc:  # noqa: BLE001 - the others still go ahead
                result.status, result.detail = "failed", f"{type(exc).__name__}: {exc}"
                log_line(f"{step.id}: {result.detail}")
                break
        results.append(result)
    return results


def _do(action: Action, system: System, result: Result, log_line: Callable[[str], None]) -> None:
    if isinstance(action, Write):
        if system.read(action.path) == action.content:
            result.items[action.path] = "unchanged"
        else:
            system.write(action.path, action.content, action.mode)
            result.items[action.path] = "written"
        log_line(f"{result.items[action.path]} {action.path}")
    elif isinstance(action, Remove):
        result.items[action.path] = "removed" if system.remove(action.path) else "unchanged"
        log_line(f"{result.items[action.path]} {action.path}")
    elif isinstance(action, Run):
        code, output = system.run(action.argv)
        log_line(f"ran {shlex.join(action.argv)} -> {code}")
        if code != 0:
            raise RuntimeError(f"{shlex.join(action.argv[:2])} answered {code}: {output.strip()}")
    else:
        _load(action, system, result, log_line)


def _load(action: Load, system: System, result: Result, log_line: Callable[[str], None]) -> None:
    found = []
    modprobe = system.tool("modprobe")
    for name in action.modules:
        if system.loaded(name):
            answer = LOADED
        elif modprobe is None:
            answer = FAILED
        else:
            code, output = system.run((modprobe, name))
            answer = classify(code, output)
        if answer == LOADED and action.first and not _board_chip(system):
            answer = ABSENT  # loaded (built in, say), but no chip came of it
        result.items[name] = answer
        log_line(f"driver {name}: {answer}")
        if answer == LOADED:
            found.append(name)
            if action.first:
                break
    if not found:
        return
    names = persisted(system.read(action.persist))
    names += [name for name in found if name not in names]
    text = modules_text(names)
    if system.read(action.persist) != text:
        system.write(action.persist, text, 0o644)
        log_line(f"written {action.persist}")


def _board_chip(system: System) -> bool:
    from libre_panel.sensors.hwmon import kind_of

    return any(kind_of(name) == "board" for name in system.chips())


class RealSystem:
    """The computer this runs on, as root."""

    def read(self, path: str) -> str | None:
        try:
            return Path(path).read_text(encoding="utf-8")
        except OSError:
            return None

    def write(self, path: str, content: str, mode: int) -> None:
        target = Path(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        handle, name = tempfile.mkstemp(dir=target.parent, prefix=".libre-panel-")
        try:
            with os.fdopen(handle, "w", encoding="utf-8") as out:
                out.write(content)
            os.chmod(name, mode)
            os.replace(name, target)  # a rules file is never half written
        except BaseException:
            Path(name).unlink(missing_ok=True)
            raise

    def remove(self, path: str) -> bool:
        try:
            Path(path).unlink()
        except FileNotFoundError:
            return False
        return True

    def run(self, argv: Sequence[str]) -> tuple[int, str]:
        env = {"PATH": SYSTEM_PATH, "LC_ALL": "C"}  # modprobe's words are the same everywhere
        done = subprocess.run(
            list(argv), capture_output=True, text=True, env=env, timeout=60, check=False
        )
        return done.returncode, (done.stdout + done.stderr)

    def loaded(self, module: str) -> bool:
        return Path("/sys/module", module.replace("-", "_")).is_dir()

    def tool(self, name: str) -> str | None:
        return shutil.which(name, path=SYSTEM_PATH)

    def chips(self) -> tuple[str, ...]:
        from libre_panel.sensors import hwmon

        return tuple(chip.name for chip in hwmon.scan())


# -- looking at the computer -----------------------------------------------------------------


def _text(path: Path) -> str | None:
    try:
        return path.read_text(encoding="utf-8", errors="replace").strip()
    except OSError:
        return None


def _module_names(text: str) -> list[str]:
    return [name.replace("-", "_") for name in re.findall(r"[A-Za-z0-9_-]+", text)]


def _available(root: Path, release: str, loaded: frozenset[str]) -> frozenset[str]:
    """Which of the drivers the setup knows this kernel has: loaded or built in, or a file
    ``modules.dep`` lists."""
    wanted = {*KNOWN_MODULES, DRIVE_MODULE}
    found = {name for name in wanted if name in loaded or (root / "sys/module" / name).is_dir()}
    for base in ("lib/modules", "usr/lib/modules"):
        for listing in ("modules.dep", "modules.builtin"):
            text = _text(root / base / release / listing) if release else None
            for name in wanted - found:
                if text and re.search(rf"(?m)(?:^|/){name}\.ko(?:\.\w+)?(?::|$)", text):
                    found.add(name)
    return frozenset(found)


def _recorded(root: Path) -> tuple[str, ...]:
    """The drivers ``sensors-detect`` recorded: its ``/etc/sysconfig/lm_sensors``, the chip
    drivers it appends to ``/etc/modules``, and ``/etc/modules-load.d/lm_sensors.conf``."""
    names: list[str] = []
    sysconfig = _text(root / "etc/sysconfig/lm_sensors") or ""
    for found in re.findall(r'(?m)^\s*HWMON_MODULES="([^"]*)"', sysconfig):
        names += _module_names(found)
    modules = _text(root / "etc/modules") or ""
    if "# Chip drivers" in modules:
        names += _module_names("\n".join(modules.split("# Chip drivers", 1)[1].splitlines()))
    for name in (_text(root / "etc/modules-load.d/lm_sensors.conf") or "").splitlines():
        names += _module_names(name.split("#", 1)[0])
    return tuple(dict.fromkeys(name for name in names if name in RECORDED_DRIVERS))


def _panel(root: Path, can: Callable[[Path, int], bool]) -> tuple[bool, bool]:
    """``(a TURZX panel is plugged in, this user may not open it)``."""
    folder = root / "sys/bus/usb/devices"
    try:
        entries = sorted(os.listdir(folder))
    except OSError:
        return False, False
    present = denied = False
    for entry in entries:
        if (_text(folder / entry / "idVendor") or "").lower() != USB_VENDOR:
            continue
        present = True
        bus, number = _text(folder / entry / "busnum"), _text(folder / entry / "devnum")
        if bus and number and bus.isdigit() and number.isdigit():
            node = root / "dev/bus/usb" / f"{int(bus):03d}" / f"{int(number):03d}"
            if node.exists() and not can(node, os.R_OK | os.W_OK):
                denied = True
    return present, denied


def _rule_elsewhere(root: Path) -> bool:
    """Whether a rules file other than ours already gives the panels to the person."""
    ours = Path(UDEV_RULES).name
    pattern = re.compile(rf'idVendor\}}=="{USB_VENDOR}"', re.IGNORECASE)
    for folder in RULE_FOLDERS:
        try:
            names = sorted(os.listdir(root / folder.lstrip("/")))
        except OSError:
            continue
        for name in names:
            if name.endswith(".rules") and name != ours:
                text = _text(root / folder.lstrip("/") / name) or ""
                if pattern.search(text) and ("uaccess" in text or "MODE" in text):
                    return True
    return False


def _energy_files(root: Path) -> list[Path]:
    folder = root / POWERCAP.lstrip("/")
    try:
        zones = sorted(os.listdir(folder))
    except OSError:
        return []
    return [folder / zone / "energy_uj" for zone in zones if (folder / zone / "energy_uj").exists()]


def gather(
    root: Path = Path("/"),
    can: Callable[[Path, int], bool] = os.access,
    which: Callable[[str], str | None] | None = None,
) -> Facts:
    """Look at this computer (``root``: where its files are; a test makes one in a folder)."""
    from libre_panel.sensors import hwmon

    root = Path(root)
    release = os.uname().release if hasattr(os, "uname") else ""
    dmi_folder = root / "sys/class/dmi/id"
    dmi = {}
    for name in ("board_vendor", "board_name", "sys_vendor", "product_name", "chassis_type"):
        found = _text(dmi_folder / name)
        if found:
            dmi[name] = found
    loaded = frozenset(
        line.split()[0]
        for line in (_text(root / "proc/modules") or "").splitlines()
        if line.strip()
    )
    energy = _energy_files(root)

    def closed(path: Path) -> bool:
        try:
            return not path.stat().st_mode & 0o004
        except OSError:
            return False

    try:
        sata = any(
            (_text(root / "sys/block" / disk / "device/vendor") or "") == "ATA"
            for disk in os.listdir(root / "sys/block")
            if disk.startswith("sd")
        )
    except OSError:
        sata = False
    present, denied = _panel(root, can)
    find = which or (lambda name: shutil.which(name, path=SYSTEM_PATH))
    return Facts(
        dmi=dmi,
        loaded=loaded,
        available=_available(root, release, loaded),
        hwmon=tuple(chip.name for chip in hwmon.scan(root)),
        recorded=_recorded(root),
        disks=sata,
        rapl=tuple(str(path) for path in energy),
        rapl_closed=tuple(str(path) for path in energy if closed(path)),
        panel=present,
        panel_denied=denied,
        rule_elsewhere=_rule_elsewhere(root),
        files={path: _text(root / path.lstrip("/")) for path in OWN_FILES},
        tools={name: found for name in ("udevadm", "modprobe", "chmod") if (found := find(name))},
    )


# -- what the app and the command line tell ------------------------------------------------------


@dataclass(frozen=True)
class Offer:
    """A thing the setup could bring: ``usb`` (the panel), ``fans`` (the mainboard's chip),
    ``drives`` (SATA temperatures) or ``power`` (the processor's, only if asked for).
    ``needed``: something is missing now that this brings."""

    id: str
    needed: bool
    modules: tuple[str, ...] = ()
    opt_in: bool = False


def offers(facts: Facts) -> list[Offer]:
    """What the setup would bring this computer, if the person agreed to everything."""
    steps = {step.id: step for step in plan(facts, Options(rapl=True))}
    found = []
    if "udev" in steps:
        found.append(Offer("usb", facts.panel_denied))
    if "modules" in steps:
        found.append(Offer("fans", True, steps["modules"].modules))
    if "drives" in steps:
        found.append(Offer("drives", False, steps["drives"].modules))
    if "rapl" in steps and facts.rapl_closed:
        found.append(Offer("power", True, opt_in=True))
    return found


def hint(facts: Facts, command: str) -> str:
    """One sentence for a person who sees readings missing, saying what is the matter and the
    command that mends it; empty when the setup has nothing that is needed."""
    needed = {offer.id for offer in offers(facts) if offer.needed}
    if not needed:
        return ""
    missing = []
    if "usb" in needed:
        missing.append(t("Your user may not use the USB panel yet."))
    if "fans" in needed:
        missing.append(
            t("The driver for the fan and temperature chip of the mainboard is not loaded.")
        )
    if "power" in needed:
        missing.append(t("The processor's power needs permission to read its energy counters."))
    return " ".join(missing) + " " + t("This mends it: {command}", command=command)


# -- the command line --------------------------------------------------------------------------


def describe(step: Step) -> str:
    """What a step does, in a sentence, for the person who is about to allow it."""
    removing = any(isinstance(action, Remove) for action in step.actions)
    names = ", ".join(step.modules)
    if step.id == "udev":
        if removing:
            return t(
                "Remove the rule that lets your user use the USB panels: {path}", path=UDEV_RULES
            )
        return t("Install the rule that lets your user use the USB panels: {path}", path=UDEV_RULES)
    if step.id == "modules" and removing:
        return t(
            "Remove the list of drivers Libre Panel loads: {path} (they stay loaded until the next "
            "start)",
            path=MODULES,
        )
    if step.id == "modules":
        return t(
            "Load the driver for the fan and temperature chip of the mainboard ({names}, the first "
            "that finds its chip) and keep it for every start: {path}",
            names=names,
            path=MODULES,
        )
    if step.id == "drives":
        return t(
            "Load the driver for the temperatures of SATA drives ({names}) and keep it for every "
            "start: {path}",
            names=names,
            path=MODULES,
        )
    if step.id == "rapl" and removing:
        return t("Close the processor's energy counters again and remove {path}", path=RAPL_RULES)
    if step.id == "rapl":
        return t(
            "Let every user read the processor's energy counters, which the kernel keeps to root "
            "since 2020 because they leak what the processor computes: {path}",
            path=RAPL_RULES,
        )
    return t("Make udev read its rules again")


def _driver_answer(answer: str) -> str:
    return {
        LOADED: t("loaded and kept for every start"),
        ABSENT: t("no such chip on this board"),
        BUSY: t("the firmware keeps the chip for itself; Libre Panel does not force it"),
        MISSING: t("not part of this kernel"),
    }.get(answer, t("did not load"))


def report_lines(steps: Sequence[Step], results: Sequence[Result]) -> list[str]:
    """What happened, one line per step and one per driver tried."""
    lines = []
    by_id = {step.id: step for step in steps}
    for result in results:
        lines.append(("- " if result.status == "done" else "! ") + describe(by_id[result.id]))
        if result.status != "done":
            lines.append(f"    {result.detail}")
        for name, answer in result.items.items():
            if "/" not in name:
                lines.append(f"    {name}: {_driver_answer(answer)}")
    return lines


def report(results: Sequence[Result]) -> dict[str, Any]:
    return {
        "ok": all(result.status == "done" for result in results),
        "results": [dataclasses.asdict(result) for result in results],
    }


def run_cli(
    args: Any,
    facts: Facts | None = None,
    system: System | None = None,
    root: bool | None = None,
    stdout: Any = None,
    stderr: Any = None,
) -> int:
    """``libre-panel system-setup --linux``: say what it will do, ask for the rights (pkexec
    where there is a desktop, else sudo), do it, say what became of each step."""
    out, err = stdout or sys.stdout, stderr or sys.stderr
    if not supported():
        print(t("This setup is for Linux."), file=err)
        return 2
    if getattr(args, "status", False):  # what the app shows: no rights needed
        from libre_panel import linux_rights

        found = linux_rights.status(gather() if facts is None else facts)
        found.pop("job", None)  # a run belongs to the app that started it
        print(json.dumps(found, indent=2), file=out)
        return 0
    options = Options(
        rapl=True if args.rapl else False if args.no_rapl else None, remove=args.remove
    )
    say = err if args.json else out  # with --json standard output holds the report, only
    facts = gather() if facts is None else facts
    steps = plan(facts, options)
    if not steps:
        print(t("Nothing to do: this computer is set up as asked."), file=say)
        if args.json:
            print(json.dumps(report([])), file=out)
        return 0
    is_root = (hasattr(os, "geteuid") and os.geteuid() == 0) if root is None else root
    if args.dry_run or not is_root:  # (as root the report below says it all)
        for step in steps:
            print("- " + describe(step), file=say)
    if args.dry_run:
        return 0
    if not is_root:
        return _elevate(options, args.json, say, err)
    results = apply(steps, system or RealSystem(), lambda line: log.info("%s", line))
    for line in report_lines(steps, results):
        print(line, file=say)
    summary = report(results)
    if args.json:
        print(json.dumps(summary), file=out)
    return 0 if summary["ok"] else 1


def _elevate(options: Options, json_output: bool, say: Any, err: Any) -> int:
    """Start the same command with the rights, in this terminal (what it prints is the person's)."""
    from libre_panel import linux_rights

    way = linux_rights.elevation(
        options, linux_rights.program(), terminal=True, json_output=json_output
    )
    if not way.argv:
        print(t("Administrator rights are needed. Run this in a terminal:"), file=err)
        print(way.terminal, file=err)
        return 1
    print(t("Asking for administrator rights with {method}.", method=way.method), file=say)
    try:
        done = subprocess.run(
            list(way.argv), env=linux_rights.clean_environment(os.environ), check=False
        )
    except OSError as exc:
        print(f"{way.argv[0]}: {exc}", file=err)
        return 1
    return done.returncode
