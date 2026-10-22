"""Entry point for the standalone (PyInstaller) builds."""

import multiprocessing
import sys

from libre_panel.cli import main

if __name__ == "__main__":
    multiprocessing.freeze_support()  # a panel's own process (panel_process.py)
    sys.exit(main())
