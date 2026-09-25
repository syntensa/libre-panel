"""Entry point for the standalone (PyInstaller) builds."""

import sys

from libre_panel.cli import main

if __name__ == "__main__":
    sys.exit(main())
