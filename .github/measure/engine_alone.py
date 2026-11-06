"""MEASUREMENT (feature branch only): what an engine process costs by itself on Windows, before
any panel's pictures: the private working set (Task Manager's Memory column) and the commit of
an engine that has a simulated panel and waits, and of one with an encoder for the 12.3"'s
frames opened and waiting. What two engines cost more than one would be about this much."""

import os
import subprocess
import sys
import time
from pathlib import Path

import psutil

root = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(root / "tests"), str(root / "src")]
from fake_panel import FakePanel  # noqa: E402
from panel_socket import PanelSocket  # noqa: E402

engine = os.environ["LIBRE_PANEL_ENGINE"]


def memory(proc: subprocess.Popen) -> str:
    info = psutil.Process(proc.pid).memory_full_info()
    commit = getattr(info, "private", None)  # (Windows: the commit; elsewhere none such)
    more = "" if commit is None else f", commit {commit / 2**20:5.1f} MB"
    return f"private working set {info.uss / 2**20:5.1f} MB{more}"


socket = PanelSocket(FakePanel(pid=0x0092, native=(480, 1920)))
alone = subprocess.Popen([engine, "panel", "--pid", "0092", "--link", socket.link],
                         stdin=subprocess.PIPE, stdout=subprocess.PIPE)  # fmt: skip
time.sleep(5)
print("an engine with a panel, waiting, 5 s:  ", memory(alone), flush=True)
time.sleep(10)
print("an engine with a panel, waiting, 15 s: ", memory(alone), flush=True)
alone.kill()
encoder = subprocess.Popen(
    [engine, "encode", "--size", "1920x720", "--turn", "0", "--fps", "25", "--crf", "20",
     "--kbit", "8000", "--keyint", "250", "--preset", "superfast", "--profile", "high"],
    stdin=subprocess.PIPE, stdout=subprocess.DEVNULL,
)  # fmt: skip
time.sleep(5)
print("an engine with the 12.3\"'s encoder, waiting:", memory(encoder), flush=True)
encoder.kill()
