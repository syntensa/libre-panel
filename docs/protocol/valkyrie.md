# Valkyrie cooler screens

Applies to the screen of Valkyrie's B360-GT VK cooler (瓦尔基里), 480×480, USB `374a:a005`
(product `Vk-Copl-08`, manufacturer `vk`), a device of the maker's own class (interface 0,
bulk OUT `02` and IN `81`, 512-byte packets; WinUSB on Windows). Driver:
`rust/devices/src/valkyrie.rs`.

Sources, read in November 2026: its owner's captures of Myth.Cool, the maker's program
([issue #69](https://github.com/syntensa/libre-panel/issues/69)), with the pictures rebuilt from
them and matched to the screen; the notes on the BoWei screens of the same vendor id in
[codehz/msdisplay](https://github.com/codehz/msdisplay) (`bowei_aio/display.py`, "mode 2"), and
[emaspa/InfoPanel-linux](https://github.com/emaspa/InfoPanel-linux) (GPL-3.0),
`docs/VALKYRIE-MC360.md`, on the MC360 (`374a:a021`, 640×480) of the same protocol. Only
protocol facts were taken. Nothing here was seen on a screen by Libre Panel yet.

## Requests

A command (u16), the length of what follows (u32) and that, little-endian. The screen answers
the command with `0x8000` set, the length and what it says; a status of four bytes is 0 where
it did it.

| Command | | Sent |
|---|---|---|
| `0x1003` | hello | `45 23 01 00` (0x00012345) |
| `0x0001` | the screen's info | nothing; 28 bytes back |
| `0x0002` | backlight | one byte (Myth.Cool: 80; Libre Panel: 100) |
| `0x0004` | the device's attributes | nothing (Myth.Cool asks; Libre Panel does not) |
| `0x000F` | heartbeat | `78 56 34 12` |
| `0x1001` | a picture follows | its size (u32) |

```
OUT 03 10 04 00 00 00 45 23 01 00      IN 03 90 04 00 00 00 00 00 00 00
OUT 0F 00 04 00 00 00 78 56 34 12      IN 0F 80 04 00 00 00 00 00 00 00
```

The info, seven u32: the pixel format `0x0E` (BGR565) at 0, 16 (bits a pixel) at 4, 960 (bytes
a row) at 8, the width at 12, the height at 16, an unknown value at 20, the pixels' size
(460800) at 24. Libre Panel opens a screen only where these are 480, 480, `0x0E` and 460800.

## Pictures

`0x1001` with the size, answered; then the pixels, BGR565 little-endian (red in bits 0–4,
green 5–10, blue 11–15: red is `1F 00`), row after row, in bulk transfers of at most 128 KiB;
a transfer whose length is a multiple of 4 KiB is followed by an empty one (480×480: 131072,
empty, 131072, empty, 131072, empty, 67584).

Myth.Cool sends heartbeats many times a second and, at times, none but them; MC360's notes send
one a second while no picture goes, and so does Libre Panel. Its closing command (`0x32` with
1234 in the BoWei notes) blanks the screen: Libre Panel never sends it. Without it the screen
goes back to its own picture once Libre Panel has quit, as its owner saw (#69).

Seen on the screen with 0.9.6 (#69): a still picture stays with the heartbeat alone; while the
PC sleeps the screen shows its own picture and then goes dark, and after waking Libre Panel's
pictures come back without a restart. Its glass hides 2 pixels at the top and the bottom.

Not proven yet: the backlight's range (Libre Panel keeps it at full and dims the pixels).
