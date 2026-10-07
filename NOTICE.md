# Notices and credits

**turing-smart-screen-python** — Copyright (C) 2021 Matthieu Houdebine and
contributors, GPL-3.0-or-later. <https://github.com/mathoudebine/turing-smart-screen-python>.
The Turing/TURZX protocol knowledge (command packet format, DES key, command
ids, USB ids, panel resolutions) comes from this project. The serial drivers
follow its protocol implementations (`library/lcd/lcd_comm_rev_*.py`,
`lcd_comm_weact_*.py`): command
numbers and layouts, the bitmap formats and the commands it never needs.
Libre Panel's implementation is its own code, released under the same license.

**SPUR II** — the private project that verified the V1.x USB protocol on a 9.2"
panel, found the zero-length-packet fix, the RGBA requirement, the 480×1920
resolution and the video-mode init, and whose classic layout is the `spur-ii`
theme.

**LibreHardwareMonitor** (MPL-2.0) — optional, separate program used as a
sensor source on Windows; not bundled.

**Barlow** (Jeremy Tribby) and **JetBrains Mono** (JetBrains) — bundled fonts
under the SIL Open Font License 1.1, see `src/libre_panel/fonts/`.

**librehardwaremonitor-api** (Sab44, MIT) — the real LibreHardwareMonitor
output in `tests/data/` comes from its test fixtures.

**Open-Meteo** — weather data from <https://open-meteo.com>, CC BY 4.0.

**lian-li-linux** (MIT) — reference for Lian Li LCD protocols (not used yet).

**Pillow**, **psutil**, **pyusb**, **pycryptodome**, **pyserial**,
**libusb-package** — runtime dependencies under their own licenses.

Product names such as TURZX, Turing Smart Screen, Lian Li and WeAct are
trademarks of their respective owners; Libre Panel is not affiliated with them.
