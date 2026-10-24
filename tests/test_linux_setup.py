"""The one-click setup of Linux (linux_setup.py): what it would do on a computer (a plan from
facts, no root needed), doing it through a stand-in for the system, and looking at a computer
that is laid out in a folder.

Nothing here touches the machine the tests run on, and nothing asks for rights: the
``no_linux_setup`` fixture of conftest.py sees to that."""

import json
import os
import stat
from pathlib import Path
from types import SimpleNamespace

import pytest
from fake_sysfs import Sysfs

from libre_panel import cli, linux_rights
from libre_panel import linux_setup as setup
from libre_panel.linux_setup import (
    MODULES,
    RAPL_RULES,
    UDEV_RULES,
    Facts,
    Options,
    Remove,
    Run,
    Write,
    plan,
)

REAL_GATHER = setup.gather  # (conftest.py puts a computer with nothing in its place)
REAL_SYSTEM = setup.RealSystem  # (... and one that no test may change)

TOOLS = {
    "udevadm": "/usr/bin/udevadm",
    "modprobe": "/usr/sbin/modprobe",
    "chmod": "/usr/bin/chmod",
}
RAPL_FILES = (
    "/sys/class/powercap/intel-rapl:0/energy_uj",
    "/sys/class/powercap/intel-rapl:0:0/energy_uj",
)
ASUS = {"board_vendor": "ASUSTeK COMPUTER INC.", "chassis_type": "3", "sys_vendor": "ASUS"}


def facts(**changes):
    """A desktop PC of a maker whose boards have a Super I/O chip, with nothing set up."""
    base = {
        "dmi": ASUS,
        "available": frozenset({"nct6775", "it87", "nct6683", "drivetemp"}),
        "tools": TOOLS,
        "files": dict.fromkeys(setup.OWN_FILES),
    }
    return Facts(**{**base, **changes})


def ids(steps):
    return [step.id for step in steps]


def actions(steps, kind):
    return [action for step in steps for action in step.actions if isinstance(action, kind)]


@pytest.fixture(autouse=True)
def on_linux(monkeypatch):
    """The setup is Linux's. What it asks of a computer is laid out in a folder or given by a
    stand-in, so the tests run on any system; this one says it is Linux."""
    monkeypatch.setattr(setup, "supported", lambda: True)


# -- which driver fits the board ------------------------------------------------------------


@pytest.mark.parametrize(
    ("maker", "drivers"),
    [
        ("ASUSTeK COMPUTER INC.", ["nct6775"]),
        ("Gigabyte Technology Co., Ltd.", ["it87", "nct6775"]),
        ("Micro-Star International Co., Ltd.", ["nct6775", "nct6683", "it87"]),
        ("ASRock", ["nct6775", "it87", "nct6683"]),
        ("Biostar Group", ["nct6775", "it87"]),
        ("Supermicro", ["nct6775"]),
        ("Intel Corporation", ["nct6683"]),
    ],
)
def test_the_drivers_tried_depend_on_the_maker_of_the_board(maker, drivers):
    assert setup.board_drivers(facts(dmi={**ASUS, "board_vendor": maker})) == drivers


@pytest.mark.parametrize(
    "dmi",
    [
        {"board_vendor": "Dell Inc.", "chassis_type": "3"},  # a maker's own PC
        {"board_vendor": "LENOVO", "chassis_type": "3"},
        {"board_vendor": "ASUSTeK COMPUTER INC.", "chassis_type": "10"},  # a notebook
        {"board_vendor": "ASUSTeK COMPUTER INC.", "chassis_type": "31"},  # a convertible
        {"board_vendor": "ASUSTeK COMPUTER INC.", "chassis_type": "30"},  # a tablet
        {"board_vendor": "Intel Corporation", "sys_vendor": "QEMU", "chassis_type": "1"},
        {"board_vendor": "Oracle", "product_name": "VirtualBox", "chassis_type": "1"},
        {"board_vendor": "Microsoft Corporation", "product_name": "Virtual Machine"},
        {},  # no DMI at all (an ARM board)
    ],
)
def test_nothing_is_tried_on_a_computer_that_is_no_desktop_board(dmi):
    assert setup.board_drivers(facts(dmi=dmi)) == []


def test_a_driver_this_kernel_does_not_have_is_not_tried():
    only = frozenset({"it87", "drivetemp"})
    assert setup.board_drivers(facts(available=only, dmi={**ASUS, "board_vendor": "ASRock"})) == [
        "it87"
    ]
    assert setup.board_drivers(facts(available=frozenset())) == []


def test_a_board_whose_chip_a_driver_reads_already_needs_none():
    assert setup.board_drivers(facts(hwmon=("k10temp", "nct6798"))) == []
    # other chips are no mainboard chip
    assert setup.board_drivers(facts(hwmon=("k10temp", "nvme", "amdgpu"))) == ["nct6775"]


def test_what_sensors_detect_recorded_counts_more_than_a_guess():
    found = facts(recorded=("it87",), dmi={**ASUS, "chassis_type": "10"})
    assert setup.board_drivers(found) == ["it87"]  # even on what would be left alone
    assert setup.board_drivers(facts(recorded=("it87", "w83627ehf"))) == ["it87"]  # (this kernel)


# -- the plan -------------------------------------------------------------------------------


def test_a_new_computer_gets_the_rule_the_driver_and_a_reload():
    steps = plan(facts())
    assert ids(steps) == ["udev", "modules", "reload"]
    assert actions(steps, Write) == [Write(UDEV_RULES, setup.udev_rules_text())]
    assert 'ATTRS{idVendor}=="1cbe"' in setup.udev_rules_text()
    load = actions(steps, setup.Load)[0]
    assert load.modules == ("nct6775",) and load.first and load.persist == MODULES
    reload = [action.argv for action in actions(steps, Run)]
    assert reload == [
        ("/usr/bin/udevadm", "control", "--reload-rules"),
        ("/usr/bin/udevadm", "trigger", "--subsystem-match=usb", "--attr-match=idVendor=1cbe"),
        ("/usr/bin/udevadm", "trigger", "--subsystem-match=usb", "--attr-match=idVendor=43a8"),
        ("/usr/bin/udevadm", "trigger", "--subsystem-match=usb", "--attr-match=idVendor=345f"),
        *(
            (
                "/usr/bin/udevadm",
                "trigger",
                "--subsystem-match=usb",
                f"--attr-match=idVendor={v}",
                f"--attr-match=idProduct={p}",
            )
            for v, p in setup.USB_PRODUCTS
        ),  # (other makers' screens, by their product ids)
        ("/usr/bin/udevadm", "trigger", "--subsystem-match=hidraw"),  # (a Kraken's commands)
    ]
    assert ("4e58", "1001") in setup.USB_PRODUCTS and ("1e71", "3008") in setup.USB_PRODUCTS


def test_the_energy_counters_stay_closed_unless_asked_for():
    found = facts(rapl=RAPL_FILES, rapl_closed=RAPL_FILES)
    assert "rapl" not in ids(plan(found)) and "rapl" not in ids(plan(found, Options(rapl=None)))
    steps = plan(found, Options(rapl=True))
    assert ids(steps) == ["udev", "modules", "rapl", "reload"]
    rule = actions(steps, Write)[-1]
    assert rule.path == RAPL_RULES
    assert 'SUBSYSTEM=="powercap"' in rule.content and "/usr/bin/chmod a+r /sys%p/energy_uj" in (
        rule.content
    )
    assert "PLATYPUS" in rule.content  # and says why they are closed
    now = [run.argv for run in actions(steps, Run)][0]
    assert now == ("/usr/bin/chmod", "a+r", *RAPL_FILES)  # and open at once, no restart
    assert ("/usr/bin/udevadm", "trigger", "--subsystem-match=powercap") in [
        run.argv for run in actions(steps, Run)
    ]


def test_without_energy_counters_there_is_nothing_to_open():
    assert "rapl" not in ids(plan(facts(), Options(rapl=True)))


def test_a_rule_that_is_there_is_not_written_again():
    ours = {UDEV_RULES: setup.udev_rules_text()}
    steps = plan(facts(files={**dict.fromkeys(setup.OWN_FILES), **ours}))
    assert "udev" not in ids(steps)
    older = {UDEV_RULES: "# the first version\n"}
    assert "udev" in ids(plan(facts(files={**dict.fromkeys(setup.OWN_FILES), **older})))


def test_a_rule_that_another_file_already_gives_is_left_alone():
    assert "udev" not in ids(plan(facts(rule_elsewhere=True)))
    ours_older = {UDEV_RULES: "x\n", RAPL_RULES: None, MODULES: None}
    assert "udev" in ids(plan(facts(rule_elsewhere=True, files=ours_older)))  # ours is kept up


def test_sata_drives_get_drivetemp_when_the_kernel_has_it_and_nobody_loaded_it():
    assert ids(plan(facts(disks=True, dmi={}))) == ["udev", "drives", "reload"]
    for changes in (
        {"disks": False},
        {"available": frozenset({"nct6775"})},
        {"loaded": frozenset({"drivetemp"})},
        {"files": {**dict.fromkeys(setup.OWN_FILES), MODULES: "drivetemp\n"}},
    ):
        found = facts(**{"disks": True, "dmi": {}, **changes})
        assert "drives" not in ids(plan(found)), changes


def test_a_computer_that_is_set_up_has_nothing_left_to_do():
    first = facts(disks=True, rapl=RAPL_FILES, rapl_closed=RAPL_FILES)
    system = FakeSystem(tools=TOOLS)
    steps = plan(first, Options(rapl=True))
    results = setup.apply(steps, system)
    assert all(result.status == "done" for result in results)
    # the computer as it is now: the files, the chip found, the counters open
    after = facts(
        disks=True,
        rapl=RAPL_FILES,
        rapl_closed=(),
        hwmon=("nct6798",),
        loaded=frozenset(system.modules),
        files={path: system.files.get(path) for path in setup.OWN_FILES},
    )
    assert plan(after, Options(rapl=True)) == []
    assert plan(after) == []
    assert setup.offers(after) == []


def test_no_step_forces_anything():
    """No parameter for a driver, no setting of the firmware: a driver is loaded as it is, or
    not at all."""
    found = facts(disks=True, rapl=RAPL_FILES, rapl_closed=RAPL_FILES)
    for options in (Options(), Options(rapl=True), Options(rapl=False), Options(remove=True)):
        for step in plan(found, options):
            for action in step.actions:
                if isinstance(action, setup.Load):
                    assert all(name.isidentifier() for name in action.modules), action
                elif isinstance(action, Run):
                    assert action.argv[0].rsplit("/", 1)[-1] in ("udevadm", "chmod"), action
                text = repr(action).lower()
                for word in ("force", "ignore_resource", "enforce_resources", "insmod", "rmmod"):
                    assert word not in text, text


def test_taking_the_setup_out_removes_its_files_and_unloads_nothing():
    own = {
        UDEV_RULES: setup.udev_rules_text(),
        RAPL_RULES: "rule\n",
        MODULES: "nct6775\n",
    }
    found = facts(
        files=own, rapl=RAPL_FILES, rapl_closed=(RAPL_FILES[1],), loaded=frozenset({"nct6775"})
    )
    steps = plan(found, Options(remove=True))
    assert ids(steps) == ["udev", "modules", "rapl", "reload"]
    assert [a.path for a in actions(steps, Remove)] == [UDEV_RULES, MODULES, RAPL_RULES]
    runs = [run.argv for run in actions(steps, Run)]
    assert ("/usr/bin/chmod", "0400", RAPL_FILES[0]) in runs  # the one it had opened is closed
    assert not any("rmmod" in " ".join(argv) or argv[0].endswith("modprobe") for argv in runs)
    assert plan(facts(), Options(remove=True)) == []  # nothing there: nothing to take out


def test_closing_the_counters_again_keeps_the_rest():
    own = {**dict.fromkeys(setup.OWN_FILES), RAPL_RULES: "rule\n"}
    steps = plan(facts(files=own, rapl=RAPL_FILES), Options(rapl=False))
    assert [a.path for a in actions(steps, Remove)] == [RAPL_RULES]
    assert "udev" not in ids(
        plan(facts(files=own, rapl=RAPL_FILES, rule_elsewhere=True), Options(rapl=False))
    )


def test_what_would_be_brought_is_told_in_terms_of_what_is_missing():
    found = facts(
        disks=True,
        panel=True,
        panel_denied=True,
        rapl=RAPL_FILES,
        rapl_closed=RAPL_FILES,
    )
    offers = {offer.id: offer for offer in setup.offers(found)}
    assert list(offers) == ["usb", "fans", "drives", "power"]
    assert offers["usb"].needed and offers["fans"].needed and not offers["drives"].needed
    assert offers["fans"].modules == ("nct6775",)
    assert offers["power"].opt_in and offers["power"].needed
    quiet = {offer.id: offer.needed for offer in setup.offers(facts(rule_elsewhere=True))}
    assert quiet == {"fans": True}
    plugged = {o.id: o.needed for o in setup.offers(facts(panel=True, panel_denied=False))}
    assert plugged["usb"] is False  # the rule is missing, but the panel works: only a hint
    assert setup.offers(facts(rapl=RAPL_FILES)) != [] and "power" not in [
        o.id for o in setup.offers(facts(rapl=RAPL_FILES))
    ]  # the counters can be read already


def test_the_hint_says_what_is_wrong_and_how_to_mend_it():
    found = facts(panel=True, panel_denied=True, rapl=RAPL_FILES, rapl_closed=RAPL_FILES)
    text = setup.hint(found, "sudo libre-panel system-setup --linux")
    assert "may not use the USB panel" in text and "fan and temperature chip" in text
    assert "energy counters" in text and text.endswith("sudo libre-panel system-setup --linux")
    assert setup.hint(facts(rule_elsewhere=True, dmi={}), "x") == ""  # nothing is needed


# -- doing it ---------------------------------------------------------------------------------


class FakeSystem:
    """A computer's files and commands, in memory. ``answers`` maps a module to what modprobe
    says about it, a command's name to its answer."""

    def __init__(self, files=None, loaded=(), tools=None, answers=None, chip=True):
        self.files = dict(files or {})
        self.modes = {}
        self.modules = set(loaded)
        self.tools = TOOLS if tools is None else tools
        self.answers = answers or {}
        self.ran = []
        self.found = chip  # a driver that loads finds its chip (False: it loads and finds none)
        self.hwmon = []

    def read(self, path):
        return self.files.get(path)

    def write(self, path, content, mode):
        self.files[path] = content
        self.modes[path] = mode

    def remove(self, path):
        return self.files.pop(path, None) is not None

    def run(self, argv):
        self.ran.append(tuple(argv))
        if Path(argv[0]).name == "modprobe":
            code, text = self.answers.get(argv[1], (0, ""))
            if code == 0:
                self.modules.add(argv[1])
                if self.found and argv[1] != "drivetemp":
                    self.hwmon.append("nct6798")
            return code, text
        return self.answers.get(Path(argv[0]).name, (0, ""))

    def loaded(self, module):
        return module in self.modules

    def tool(self, name):
        return self.tools.get(name)

    def chips(self):
        return tuple(self.hwmon)


def test_the_steps_are_done_and_every_one_is_logged():
    lines = []
    system = FakeSystem()
    results = setup.apply(plan(facts()), system, lines.append)
    assert [(r.id, r.status) for r in results] == [
        ("udev", "done"),
        ("modules", "done"),
        ("reload", "done"),
    ]
    assert system.files[UDEV_RULES] == setup.udev_rules_text() and system.modes[UDEV_RULES] == 0o644
    assert system.files[MODULES].splitlines()[-1] == "nct6775"
    assert ("/usr/sbin/modprobe", "nct6775") in system.ran
    assert any("written /etc/udev/rules.d/60-libre-panel.rules" in line for line in lines)
    assert any("driver nct6775: loaded" in line for line in lines)
    assert any(line.startswith("ran /usr/bin/udevadm control --reload-rules") for line in lines)


def test_doing_it_twice_changes_nothing_the_second_time():
    system = FakeSystem()
    steps = plan(facts())
    setup.apply(steps, system)
    ran, files = len(system.ran), dict(system.files)
    again = setup.apply(plan(facts()), system)  # the same plan again, from a stale reading
    assert system.files == files
    assert again[0].items == {UDEV_RULES: "unchanged"}
    udevadm = 2 + len(setup.USB_VENDORS) + len(setup.USB_PRODUCTS)  # reload, then the triggers
    assert again[1].items == {"nct6775": "loaded"} and len(system.ran) == ran + udevadm  # only


def test_the_first_driver_that_finds_its_chip_ends_the_search():
    system = FakeSystem(answers={"it87": (0, "")})
    found = facts(dmi={**ASUS, "board_vendor": "Gigabyte"})
    results = setup.apply(plan(found), system)
    assert results[1].items == {"it87": "loaded"}
    assert ("/usr/sbin/modprobe", "nct6775") not in system.ran


def test_a_driver_without_its_chip_is_no_error_and_is_not_kept():
    system = FakeSystem(
        answers={
            "it87": (1, "modprobe: ERROR: could not insert 'it87': No such device"),
            "nct6775": (1, "modprobe: ERROR: could not insert 'nct6775': No such device"),
        }
    )
    found = facts(dmi={**ASUS, "board_vendor": "Gigabyte"})
    results = setup.apply(plan(found), system)
    assert results[1].status == "done"
    assert results[1].items == {"it87": "none", "nct6775": "none"}
    assert MODULES not in system.files  # nothing found, nothing to load at every start


@pytest.mark.parametrize(
    ("code", "text", "kind"),
    [
        (0, "", "loaded"),
        (1, "modprobe: ERROR: could not insert 'it87': No such device", "none"),
        (1, "modprobe: ERROR: could not insert 'nct6775': Device or resource busy", "busy"),
        (1, "modprobe: FATAL: Module nct6683 not found in directory /lib/modules/6.1", "missing"),
        (1, "insmod: ERROR: could not load module: No such file or directory", "missing"),
        (1, "modprobe: ERROR: could not insert 'it87': Invalid argument", "failed"),
    ],
)
def test_what_modprobe_says_is_told_apart(code, text, kind):
    assert setup.classify(code, text) == kind


def test_a_chip_the_firmware_keeps_for_itself_is_said_and_not_forced():
    system = FakeSystem(answers={"nct6775": (1, "Device or resource busy")})
    results = setup.apply(plan(facts()), system)
    assert results[1].items == {"nct6775": "busy"}
    assert all("acpi" not in " ".join(argv) for argv in system.ran)
    assert "does not force it" in " ".join(setup.report_lines(plan(facts()), results))


def test_the_drivers_kept_from_before_are_kept_with_the_new_one():
    system = FakeSystem(files={MODULES: setup.modules_text(["drivetemp"])})
    found = facts(files={MODULES: system.files[MODULES], UDEV_RULES: None, RAPL_RULES: None})
    setup.apply(plan(found), system)
    assert setup.persisted(system.files[MODULES]) == ["drivetemp", "nct6775"]


def test_a_driver_that_loads_but_finds_no_chip_is_no_find():
    """A driver that is built into the kernel is "loaded" without a chip: only a chip counts."""
    system = FakeSystem(chip=False)
    results = setup.apply(plan(facts()), system)
    assert results[1].items == {"nct6775": "none"} and MODULES not in system.files


def test_a_driver_that_is_loaded_already_is_kept_without_loading_it_again():
    system = FakeSystem(loaded={"nct6775"}, chip=True)
    system.hwmon.append("nct6798")
    setup.apply([setup.Step("modules", (setup.Load(("nct6775",), first=True),))], system)
    assert system.ran == [] and setup.persisted(system.files[MODULES]) == ["nct6775"]


def test_one_step_failing_leaves_the_others_to_go_on():
    system = FakeSystem(answers={"udevadm": (1, "Failed to send reload request")})
    results = setup.apply(plan(facts()), system)
    assert [r.status for r in results] == ["done", "done", "failed"]
    assert "udevadm control answered 1" in results[2].detail
    summary = setup.report(results)
    assert summary["ok"] is False and summary["results"][2]["id"] == "reload"
    text = "\n".join(setup.report_lines(plan(facts()), results))
    assert "! Make udev read its rules again" in text and "Failed to send reload request" in text


def test_a_missing_tool_is_a_failure_of_its_step_not_of_the_setup():
    system = FakeSystem(tools={})
    steps = plan(facts(tools={}))
    results = setup.apply(steps, system)
    assert results[1].items == {"nct6775": "failed"}


def test_a_write_that_fails_says_so_and_goes_on():
    class Locked(FakeSystem):
        def write(self, path, content, mode):
            if path == UDEV_RULES:
                raise PermissionError("read-only file system")
            super().write(path, content, mode)

    results = setup.apply(plan(facts()), Locked())
    assert results[0].status == "failed" and "read-only file system" in results[0].detail
    assert results[1].status == "done"


def test_the_real_system_writes_whole_files_and_takes_them_out(tmp_path):
    real = REAL_SYSTEM()
    target = str(tmp_path / "etc" / "udev" / "60-test.rules")
    assert real.read(target) is None
    real.write(target, "one\n", 0o644)
    real.write(target, "two\n", 0o644)
    assert real.read(target) == "two\n"
    assert [p.name for p in (tmp_path / "etc" / "udev").iterdir()] == ["60-test.rules"]
    if os.name == "posix":
        assert stat.S_IMODE(os.stat(target).st_mode) == 0o644
    assert real.remove(target) is True and real.remove(target) is False


@pytest.mark.skipif(os.name != "posix", reason="runs a shell")
def test_the_real_system_runs_commands_with_a_plain_path(tmp_path):
    real = REAL_SYSTEM()
    assert real.run(["sh", "-c", "echo hello; exit 3"]) == (3, "hello\n")
    code, text = real.run(["sh", "-c", "echo $PATH; echo $LC_ALL"])
    assert text.splitlines() == [setup.SYSTEM_PATH, "C"]
    assert real.tool("sh") is not None and real.tool("no-such-tool-here") is None


# -- looking at a computer -----------------------------------------------------------------------


@pytest.mark.skipif(not hasattr(os, "uname"), reason="the kernel's release")
def test_a_computer_in_a_folder_is_read(tmp_path):
    root = Sysfs(tmp_path / "pc")
    release = os.uname().release
    root.write("sys/class/dmi/id/board_vendor", "ASUSTeK COMPUTER INC.")
    root.write("sys/class/dmi/id/chassis_type", "3")
    root.write("sys/class/dmi/id/product_serial", "SECRET")  # (not asked for)
    root.write("proc/modules", "snd 1 0 - Live 0x0\nnvme_core 2 1 - Live 0x0\n")
    root.write(f"lib/modules/{release}/modules.dep", "kernel/drivers/hwmon/nct6775.ko.zst: x\n")
    root.write(f"lib/modules/{release}/modules.builtin", "kernel/drivers/hwmon/drivetemp.ko\n")
    root.hwmon(0, "k10temp", temp1_input=40000)
    root.write("etc/sysconfig/lm_sensors", 'HWMON_MODULES="it87 jc42"\n')
    root.write("etc/modules", "loop\n# Chip drivers\nw83627ehf\n")
    root.write("sys/block/sda/device/vendor", "ATA     ")
    root.write("sys/block/sdb/device/vendor", "Generic ")
    root.powercap("intel-rapl:0", "package-0", energy=5)
    root.write("etc/udev/rules.d/60-libre-panel.rules", "# ours\n")
    root.write(
        "etc/udev/rules.d/99-other.rules",  # (a rule that gives every kind of USB panel)
        'ATTRS{idVendor}=="1CBE", TAG+="uaccess"\nATTRS{idVendor}=="43a8", TAG+="uaccess"\n'
        'ATTRS{idVendor}=="345f", TAG+="uaccess"\n'
        'ATTRS{idVendor}=="0416", ATTRS{idProduct}=="5302", TAG+="uaccess"\n'
        'ATTRS{idVendor}=="0416", ATTRS{idProduct}=="5408", TAG+="uaccess"\n'
        'ATTRS{idVendor}=="4e58", ATTRS{idProduct}=="1001", TAG+="uaccess"\n'
        + "".join(
            f'ATTRS{{idVendor}}=="1e71", ATTRS{{idProduct}}=="{pid}", TAG+="uaccess"\n'
            for pid in ("3008", "300e", "3012", "3014")
        ),
    )
    root.write("etc/modules-load.d/libre-panel.conf", "nct6775\n")
    for name in ("sys/class/powercap/intel-rapl:0/energy_uj",):
        os.chmod(root.root / name, 0o400)
    found = REAL_GATHER(root.root, which=lambda name: {"udevadm": "/u/udevadm"}.get(name))
    assert found.dmi == {"board_vendor": "ASUSTeK COMPUTER INC.", "chassis_type": "3"}
    assert found.loaded == {"snd", "nvme_core"}
    assert {"nct6775", "drivetemp"} <= found.available and "nct6683" not in found.available
    assert found.hwmon == ("k10temp",)
    assert found.recorded == ("it87", "w83627ehf")
    assert found.disks is True
    if os.name == "posix":
        assert (
            found.rapl_closed
            == found.rapl
            == (str(root.root / "sys/class/powercap/intel-rapl:0/energy_uj"),)
        )
    assert found.rule_elsewhere is True
    assert found.files[UDEV_RULES] == "# ours" and found.files[MODULES] == "nct6775"
    assert found.files[RAPL_RULES] is None
    assert found.tools == {"udevadm": "/u/udevadm"}


@pytest.mark.skipif(not hasattr(os, "uname"), reason="the kernel's release")
def test_a_drivers_that_is_loaded_or_built_in_is_available(tmp_path):
    root = Sysfs(tmp_path / "pc")
    root.write("sys/module/it87/version", "1")
    found = REAL_GATHER(root.root, which=lambda name: None)
    assert "it87" in found.available and "nct6775" not in found.available


def test_a_panel_the_person_may_not_open_is_told(tmp_path):
    root = Sysfs(tmp_path / "pc")
    for number, (vendor, bus, device) in enumerate([("1cbe", 1, 5), ("8087", 1, 2)]):
        base = f"sys/bus/usb/devices/1-{number}"
        root.write(f"{base}/idVendor", vendor)
        root.write(f"{base}/busnum", bus)
        root.write(f"{base}/devnum", device)
    root.write("dev/bus/usb/001/005", "")
    denied = REAL_GATHER(root.root, can=lambda path, mode: False, which=lambda name: None)
    assert (denied.panel, denied.panel_denied) == (True, True)
    allowed = REAL_GATHER(root.root, can=lambda path, mode: True, which=lambda name: None)
    assert (allowed.panel, allowed.panel_denied) == (True, False)
    nothing = REAL_GATHER(tmp_path / "empty", which=lambda name: None)
    assert (nothing.panel, nothing.panel_denied, nothing.dmi, nothing.disks) == (
        False,
        False,
        {},
        False,
    )


def test_a_screen_whose_vendor_has_other_devices_too_counts_by_its_product(tmp_path):
    root = Sysfs(tmp_path / "pc")
    root.write("sys/bus/usb/devices/1-1/idVendor", "0416")  # (Winbond's: not a screen)
    root.write("sys/bus/usb/devices/1-1/idProduct", "5020")
    assert REAL_GATHER(root.root, which=lambda name: None).panel is False
    root.write("sys/bus/usb/devices/1-2/idVendor", "0416")  # a Trofeo Vision bar
    root.write("sys/bus/usb/devices/1-2/idProduct", "5408")
    assert REAL_GATHER(root.root, which=lambda name: None).panel is True


def test_the_command_line_says_what_it_will_do(capsys):
    args = SimpleNamespace(rapl=False, no_rapl=False, remove=False, dry_run=True, json=False)
    code = setup.run_cli(args, facts=facts(), root=False)
    out = capsys.readouterr().out
    assert code == 0
    assert "- Install the rule that lets your user use the USB panels: " + UDEV_RULES in out
    assert "(nct6775, the first that finds its chip)" in out
    assert "Make udev read its rules again" in out


def test_a_computer_that_is_set_up_is_told_so(capsys):
    args = SimpleNamespace(rapl=False, no_rapl=False, remove=False, dry_run=False, json=True)
    code = setup.run_cli(args, facts=facts(rule_elsewhere=True, dmi={}), root=False)
    captured = capsys.readouterr()
    assert code == 0 and json.loads(captured.out) == {"ok": True, "results": []}
    assert "Nothing to do" in captured.err  # (with --json the words go to the other stream)


def test_as_root_it_does_it_and_reports_in_words_and_in_json(capsys):
    system = FakeSystem()
    args = SimpleNamespace(rapl=False, no_rapl=False, remove=False, dry_run=False, json=True)
    code = setup.run_cli(args, facts=facts(), system=system, root=True)
    captured = capsys.readouterr()
    report = json.loads(captured.out)
    assert code == 0 and report["ok"] is True
    assert [r["id"] for r in report["results"]] == ["udev", "modules", "reload"]
    assert report["results"][1]["items"] == {"nct6775": "loaded"}
    assert "nct6775: loaded and kept for every start" in captured.err
    assert UDEV_RULES in system.files


def test_a_failing_step_makes_the_exit_code_one(capsys):
    system = FakeSystem(answers={"udevadm": (1, "no")})
    args = SimpleNamespace(rapl=False, no_rapl=False, remove=False, dry_run=False, json=False)
    assert setup.run_cli(args, facts=facts(), system=system, root=True) == 1
    assert "! Make udev read its rules again" in capsys.readouterr().out


def test_without_the_rights_the_command_asks_for_them_in_the_terminal(monkeypatch, capsys):
    asked = []

    def run(argv, **kwargs):
        asked.append((argv, kwargs["env"]))
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr(setup.subprocess, "run", run)
    monkeypatch.setattr(linux_rights, "program", lambda *a, **k: ["/opt/lp/libre-panel"])
    monkeypatch.setattr(linux_rights.shutil, "which", lambda name: f"/usr/bin/{name}")
    monkeypatch.setenv("DISPLAY", ":0")
    args = SimpleNamespace(rapl=True, no_rapl=False, remove=False, dry_run=False, json=False)
    assert (
        setup.run_cli(args, facts=facts(rapl=RAPL_FILES, rapl_closed=RAPL_FILES), root=False) == 0
    )
    [(argv, env)] = asked
    assert argv == [
        "/usr/bin/pkexec",
        "--disable-internal-agent",
        "/opt/lp/libre-panel",
        "system-setup",
        "--linux",
        "--rapl",
    ]
    assert "LD_LIBRARY_PATH" not in env
    out = capsys.readouterr().out
    assert "- Let every user read the processor's energy counters" in out  # told before asking
    assert "Asking for administrator rights with pkexec." in out


def test_where_no_rights_can_be_asked_for_the_terminal_command_is_given(monkeypatch, capsys):
    monkeypatch.setattr(linux_rights, "program", lambda *a, **k: None)
    args = SimpleNamespace(rapl=False, no_rapl=False, remove=False, dry_run=False, json=False)
    assert setup.run_cli(args, facts=facts(), root=False) == 1
    err = capsys.readouterr().err
    assert (
        "Administrator rights are needed" in err and "sudo libre-panel system-setup --linux" in err
    )


def test_the_command_is_not_for_other_systems(monkeypatch, capsys):
    monkeypatch.setattr(setup, "supported", lambda: False)
    args = SimpleNamespace(rapl=False, no_rapl=False, remove=False, dry_run=True, json=False)
    assert setup.run_cli(args, facts=facts()) == 2
    assert "for Linux" in capsys.readouterr().err


def test_the_parser_has_the_command(monkeypatch, capsys):
    monkeypatch.setattr(setup, "gather", lambda *a, **k: facts())
    assert cli.main(["system-setup", "--linux", "--dry-run"]) == 0
    assert "USB panels" in capsys.readouterr().out
    with pytest.raises(SystemExit) as stop:
        cli.main(["system-setup", "--rapl", "--no-rapl"])  # one or the other
    assert stop.value.code == 2
    parsed = cli.build_parser().parse_args(["system-setup", "--linux", "--remove", "--json"])
    assert (parsed.linux, parsed.remove, parsed.json, parsed.rapl) == (True, True, True, False)


def windows_run(monkeypatch):
    """Windows' side of the command, recorded: ``(arguments, platform)`` of each call."""
    from libre_panel.sensorservice import setup as windows

    seen = []
    monkeypatch.setattr(
        windows, "run_command", lambda args, platform=None: seen.append((args, platform)) or 0
    )
    return seen


def test_it_is_one_command_for_both_systems_and_linux_takes_its_side_for_granted(
    monkeypatch, capsys
):
    monkeypatch.setattr(setup, "gather", lambda *a, **k: facts())
    seen = windows_run(monkeypatch)
    monkeypatch.setattr(cli, "_platform", lambda: "linux")
    assert cli.main(["system-setup", "--dry-run"]) == 0  # no --linux needed on Linux
    assert "USB panels" in capsys.readouterr().out and seen == []
    installed = {**dict.fromkeys(setup.OWN_FILES), UDEV_RULES: setup.udev_rules_text()}
    monkeypatch.setattr(setup, "gather", lambda *a, **k: facts(files=installed))
    assert cli.main(["system-setup", "--remove", "--dry-run"]) == 0
    assert "Remove the rule" in capsys.readouterr().out and seen == []
    assert cli.main(["system-setup", "--service", "--pawnio"]) == 0  # a flag of Windows': its side
    [(args, system)] = seen
    assert (args.service, args.pawnio, system) == (True, True, "linux")  # (which says so)


def test_windows_gets_its_own_side_and_the_flags_of_linux_are_refused_there(monkeypatch, capsys):
    seen = windows_run(monkeypatch)
    monkeypatch.setattr(cli, "_platform", lambda: "win32")
    assert cli.main(["system-setup", "--service"]) == 0 and seen[0][1] == "win32"
    for flags in (["--rapl"], ["--no-rapl"], ["--dry-run"], ["--json"], ["--service", "--json"]):
        assert cli.main(["system-setup", *flags]) == 2
        assert "is for Linux (--linux)" in capsys.readouterr().err
    assert len(seen) == 1
    monkeypatch.setattr(setup, "supported", lambda: False)
    assert cli.main(["system-setup", "--linux"]) == 2  # asked for Linux's, on another system
    assert "for Linux" in capsys.readouterr().err


def test_the_flags_of_windows_are_refused_with_linux(monkeypatch, capsys):
    seen = windows_run(monkeypatch)
    monkeypatch.setattr(cli, "_platform", lambda: "linux")
    for flags in (["--service"], ["--pawnio", "--perf-log-users"], ["--user-sid", "S-1-5-21-1"]):
        assert cli.main(["system-setup", "--linux", *flags]) == 2
        assert "is for Windows, not for --linux" in capsys.readouterr().err
    assert seen == []


def test_the_status_says_what_is_in_place_and_asks_for_nothing(monkeypatch, capsys):
    monkeypatch.setattr(linux_rights, "program", lambda *a, **k: ["/opt/lp/libre-panel"])
    args = SimpleNamespace(rapl=False, no_rapl=False, remove=False, dry_run=False, json=False)
    args.status = True
    code = setup.run_cli(args, facts=facts(panel=True, panel_denied=True), root=False)
    said = json.loads(capsys.readouterr().out)
    assert code == 0 and said["platform"] == "linux" and said["available"] is True
    assert [offer["id"] for offer in said["offers"]] == ["usb", "fans"]
    assert said["installed"] == []
    assert said["command"] == "sudo /opt/lp/libre-panel system-setup --linux"
    assert "job" not in said  # (a run belongs to the app that started it)


def test_the_rules_are_the_ones_the_udev_rules_command_prints(capsys):
    assert cli.main(["udev-rules"]) == 0
    assert capsys.readouterr().out == setup.udev_rules_text()
