# Serial panel protocols

Libre Panel's serial drivers follow the protocol implementations of
[turing-smart-screen-python](https://github.com/mathoudebine/turing-smart-screen-python)
(`library/lcd/lcd_comm_rev_*.py`, `lcd_comm_weact_*.py`, GPL-3.0), which
were worked out on the hardware. Nobody has run Libre Panel on these panels
yet, so they are listed as "unverified" in [HARDWARE.md](../HARDWARE.md).
`libre-panel doctor` and a report are what changes that.

All of them run at 115200 baud with RTS/CTS flow control and show up as a
serial port. Libre Panel finds the port by USB id and serial number
(`device.port` names it if that is not enough). Each driver sends only
display commands: no reset, no firmware, nothing the panel stores. The
module docstrings in `src/libre_panel/devices/` hold the byte-level details.
Simulated panels in `tests/fake_serial.py` read the byte stream as the
devices do.

| Family | Panels | USB id (serial number) | Bitmaps | Brightness | Never sent |
|---|---|---|---|---|---|
| rev. A (`turing_rev_a.py`) | Turing 3.5", UsbPCMonitor 3.5"/5"/7" | 1a86:5722 (`USB35INCHIPS…`) | 6-byte command, box packed 10 bits per number; RGB565 little endian | 110: 0 brightest – 255 darkest | 101 reset, 102 clear, 103, 108, 109 |
| rev. B (`turing_rev_b.py`) | XuanFang 3.5", flagship | 1a86:5722 (`2017-2-25`) | 10-byte packets framed by the command; box 16-bit big endian; RGB565 big endian; 50 ms pause after each | 0–255, or only on/off (HELLO says which) | 0xCD backplate LED |
| rev. C (`turing_rev_c.py`) | Turing 2.1", 5", 8.8" (V0.x) | asleep 1a86:ca21 (`CT21INCH`, `USB7INCH`, `CT88INCH`); awake 0525:a4a7, 1d6b:0121/0106 (`20080411`) | 250-byte blocks; whole frames BGRA, boxes row by row at framebuffer offsets (BGR or BGRA by size and ROM) | 0–255 | RESTART, TURNOFF/TURNON, OPTIONS |
| rev. D (`kipye_rev_d.py`) | Kipye Qiye 3.5" | 454d:4e41 | box x0, x1, y0, y1 big endian; RGB565 big endian in 64-byte packets (0x50 + 63) | 0–500, sent twice | fill colour, mirrored / upside-down modes |
| WeAct (`weact.py`) | Display FS V1 3.5", 0.96" | 1a86:fe0c (`AB…` 3.5", `AD…` 0.96") | commands end with 0x0A; box 16-bit little endian; RGB565 little endian | 0–255 with a fade time | fill, sensor reports, release |

## Where Libre Panel differs from the reference

- **Rev. C, OPTIONS.** The reference sends it to set the orientation; it
  also stores the panel's start mode and sleep interval, which the owner
  may have set in the vendor app. Rev. C turns frames in software, so
  Libre Panel leaves it out. If a rev. C panel stays dark without it,
  please report it.
- **Rev. C 8.8" in portrait.** For a box (not a whole frame) the reference
  computes the column from the panel's height, which leaves the 480 px wide
  framebuffer. Libre Panel puts the box where the whole frame's half turn
  puts the same pixels; the test suite checks that boxes and whole frames
  agree for every size and orientation.
- **Rev. C awake ids** are the generic Linux USB gadget ids. With
  `model = "auto"` a port with them counts only with the panels' serial
  number, so other gadgets (a Raspberry Pi in gadget mode) are left alone.
- **Partial updates.** Libre Panel sends only the box that changed; the
  reference does the same for its widgets.
- **Rev. C sizes.** The HELLO answer does not tell the size reliably (a 2.1"
  says 5inch). Libre Panel takes it from the config, from the serial number
  of the sleeping panel, or from the size of the first frame.
