"""Entry point of LibrePanel.exe (Windows release build, no console window).

Starts the tray app unless a command is given.
"""

import multiprocessing
import sys

from libre_panel.cli import main

if __name__ == "__main__":
    multiprocessing.freeze_support()  # a panel's own process (panel_process.py)
    sys.exit(main(default_command="tray"))
