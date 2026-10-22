import multiprocessing

from libre_panel.cli import main

if __name__ == "__main__":  # (a panel's own process starts here too: panel_process.py)
    multiprocessing.freeze_support()
    raise SystemExit(main())
