"""A Linux PC's /sys in a folder, for the tests of the Linux sensors.

``Sysfs(tmp_path)`` lays out chips, graphics cards, drives and energy counters the way the
kernel's drivers do (the names of the files are theirs); the sources take the folder as the root
of the file system. Every model and name in here is made up, none belongs to whoever runs the
tests."""

from pathlib import Path


class Sysfs:
    def __init__(self, root: Path):
        self.root = Path(root)

    def write(self, relative: str, text: object) -> Path:
        path = self.root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(f"{text}\n", encoding="utf-8")
        return path

    def set(self, relative: str, text: object) -> None:
        """Change a file that is there (a sensor that has a new value)."""
        path = self.root / relative
        assert path.exists(), relative
        path.write_text(f"{text}\n", encoding="utf-8")

    # -- hwmon ---------------------------------------------------------------------------

    def hwmon(self, number: int, name: str, device: bool = False, **files: object) -> str:
        """``sys/class/hwmon/hwmon<number>`` with the chip's ``name`` and files, e.g.
        ``temp1_input=45000, temp1_label="Tctl", fan1_input=900``. ``device``: the files are one
        level down, in ``device/`` (older kernels)."""
        folder = f"sys/class/hwmon/hwmon{number}"
        self.write(f"{folder}/name", name)
        for file, value in files.items():
            self.write(f"{folder}/{'device/' if device else ''}{file}", value)
        return folder

    def thermal_zone(self, number: int, kind: str, millidegrees: int) -> str:
        folder = f"sys/class/thermal/thermal_zone{number}"
        self.write(f"{folder}/type", kind)
        self.write(f"{folder}/temp", millidegrees)
        return folder

    # -- graphics cards --------------------------------------------------------------------

    def card(
        self,
        number: int,
        vendor: int,
        product: int,
        driver: str,
        slot: str,
        vram: int | None = None,
        hwmon: int | None = None,
        status: str | None = None,
        **files: object,
    ) -> str:
        """``sys/class/drm/card<number>`` with a PCI device behind it. ``hwmon``: the number of
        its hwmon folder (made with ``hwmon``, it gets the card's ``device/hwmon/hwmon<n>`` too),
        ``status``: its runtime power state, ``files``: more files of the device, by name."""
        folder = f"sys/class/drm/card{number}"
        device = f"{folder}/device"
        self.write(f"{device}/vendor", f"0x{vendor:04x}")
        self.write(f"{device}/device", f"0x{product:04x}")
        self.write(f"{device}/uevent", f"DRIVER={driver}\nPCI_SLOT_NAME={slot}\nPCI_ID=0:0")
        if vram is not None:
            self.write(f"{device}/mem_info_vram_total", vram)
        if hwmon is not None:
            (self.root / device / "hwmon" / f"hwmon{hwmon}").mkdir(parents=True)
        if status is not None:
            self.write(f"{device}/power/runtime_status", status)
        for file, value in files.items():
            self.write(f"{device}/{file}", value)
        (self.root / f"sys/class/drm/card{number}-DP-1").mkdir(exist_ok=True)  # a connector
        return folder

    # -- energy counters -------------------------------------------------------------------

    def powercap(self, zone: str, name: str, energy: int, wrap: int = 262143328850) -> str:
        folder = f"sys/class/powercap/{zone}"
        self.write(f"{folder}/name", name)
        self.write(f"{folder}/energy_uj", energy)
        self.write(f"{folder}/max_energy_range_uj", wrap)
        return folder


# -- typical machines ------------------------------------------------------------------------


def amd_card(sysfs: Sysfs, number: int = 0, hwmon: int = 3, slot: str = "0000:03:00.0", **files):
    """An AMD card with the files amdgpu has: temperatures edge, junction and memory, a fan,
    its power and clocks, the load and the video memory."""
    sysfs.hwmon(
        hwmon,
        "amdgpu",
        temp1_input=51000,
        temp1_label="edge",
        temp2_input=63000,
        temp2_label="junction",
        temp3_input=58000,
        temp3_label="mem",
        fan1_input=1180,
        pwm1=102,
        power1_average=142_000_000,
        freq1_input=2_400_000_000,
        freq1_label="sclk",
        freq2_input=1_124_000_000,
        freq2_label="mclk",
    )
    sysfs.card(
        number,
        0x1002,
        0x744C,
        "amdgpu",
        slot,
        vram=16 * 1024**3,
        hwmon=hwmon,
        gpu_busy_percent=37,
        mem_info_vram_used=6 * 1024**3,
        **files,
    )
