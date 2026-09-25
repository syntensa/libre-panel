# Contributing

Thanks for helping! The most valuable contributions right now:

1. **Testing hardware.** Own a panel? Run `libre-panel devices` and report what
   works — especially USB sizes other than 9.2", and any serial panel.
2. **Themes.** Build one in the editor and share it (with a license for any
   fonts/images you include).
3. **Drivers** for the families marked *planned* in [docs/HARDWARE.md](docs/HARDWARE.md).

## Development

```bash
git clone https://github.com/syntensa/libre-panel
cd libre-panel
python -m venv .venv && . .venv/bin/activate     # Windows: .venv\Scripts\activate
pip install -e ".[dev,usb]"
pytest
ruff check . && ruff format --check .
libre-panel editor
```

Please keep pull requests focused, add tests for behaviour changes, and run the
checks above. By contributing you agree that your work is licensed under
GPL-3.0-or-later.

## Hardware safety

Drivers only use display commands (frames, brightness, sync). Never add
firmware, restart or storage commands without a documented, reversible reason
and a hardware test. When in doubt, ask in an issue first.

## Where things live

See [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md).
