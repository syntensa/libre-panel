# NZXT Kraken LCD

Applies to the NZXT Krakens with an LCD on the pump: Kraken Z53, Z63 and Z73 (USB `1e71:3008`,
320×320), Kraken 2023 (`1e71:300e`, 240×240), Kraken Elite RGB 2024 (`1e71:3012`, 640×640) and
Kraken Plus 2024 (`1e71:3014`, 240×240). Driver: `src/libre_panel/devices/nzxt_kraken.py`.

Sources, read in October 2026:

- [liquidctl/liquidctl](https://github.com/liquidctl/liquidctl) (GPL-3.0), commit `292dc56`:
  `liquidctl/driver/kraken3.py` (class `KrakenZ3`: the screen's state, brightness and turn, the
  buckets, the static picture as R G B 0, the Kraken 2023's firmware 2) and
  `docs/kraken-x3-z3-guide.md`.
- [ossini/krakenlcd](https://github.com/ossini/krakenlcd) (GPL-3.0): `docs/PROTOCOL.md`,
  "verified against a Kraken Z53 (firmware 1.5.11) and cross-checked against liquidctl",
  `krakenlcd/device.py` and `krakenlcd/lowlevel.py` (hidraw for the reports, two buckets in
  turn for a picture that changes).

Nothing here was seen on a cooler by Libre Panel yet. The pump's and the fans' reports (`0x70`
to `0x73`) are never written: only `0x10`, `0x30`, `0x32`, `0x36` and `0x38`.

## Transport

| Interface | Class | Carries |
|---|---|---|
| 1 | HID | the commands and the cooler's status: reports of 64 bytes |
| 0 | vendor | the pictures: bulk OUT `0x02` (512-byte packets) |

On Linux the HID interface stays bound to `usbhid` (and to the `nzxt-kraken3` hwmon driver):
the reports are read and written on its `/dev/hidraw*` node, which is found in sysfs (the USB
device by its bus and device number, its interface of class `03`, `*/hidraw/hidraw*` below
it). Interface 0 has no kernel driver and is claimed through libusb. Elsewhere the HID
interface's interrupt endpoints are used through libusb, report id in byte 0.

## Reports

Byte 0 is the report's id, byte 1 a sub-command, zeros fill it up to 64. The answer has the id
+ 1 and the same sub-command; its byte 14 is `01` where the command was taken. The cooler sends
status reports of its own (`75 01`) in between, and other programs may read the same queue: an
answer is known by its id and sub-command, and anything else is passed by.

| Report | Meaning | Answer |
|---|---|---|
| `10 01` | the firmware's version | `11 01`: bytes 17–19 major, minor, patch (liquidctl; krakenlcd reads 16–18) |
| `30 01` | the screen's state | `31 01`: width and height at 20–23, brightness at 24, turn (quarters) at 26 |
| `30 02 01 <b> 00 00 01 <turn>` | brightness (0–100) and turn | none read |
| `30 04 <bucket>` | a bucket's state | not used |
| `32 01 <bucket> <bucket+1> <start> <size> 01` | set a bucket up (KiB, 16 bit each) | `33 01` |
| `32 02 <bucket>` | delete a bucket | `33 02`; refused for the bucket on screen |
| `36 03` | before a transfer (purpose unknown) | read, not looked at |
| `36 01 <bucket>` | arm a bucket for the transfer | read, not looked at |
| `36 02` | end of the transfer | not read |
| `38 01 02 00` | the firmware's own screen (the liquid's temperature) | `39 01` |
| `38 01 04 <bucket>` | show the picture in a bucket | `39 01` |

Brightness and turn live in one report: writing the brightness means writing the turn, so the
turn is read with `30 01` first and written back as it was (krakenlcd's warning).

## A picture into a bucket

There are 16 buckets in 24320 KiB of picture memory, which the cooler forgets when it loses
power (krakenlcd). Libre Panel uses buckets 0 and 1 in turn, each with half the memory
(krakenlcd's `DoubleBuffer`): the bucket on screen cannot be written, so the one written is
always the other. When it opens, it shows the firmware's screen and deletes both.

1. `36 03`
2. `32 02 <bucket>`, again where it was taken (a bucket that held a picture needs two)
3. `32 01 <bucket> <bucket+1> <start> <size> 01`: start 0 or 12160 KiB, the size the header and
   the pixels rounded up to KiB
4. `36 01 <bucket>`
5. on bulk `0x02`, **as a transfer of its own**, the header of 20 bytes: `12 FA 01 E8 AB CD EF
   98 76 54 32 10`, the kind `02 00 00 00` (raw pixels; `01` is a GIF file), the pixels' length
   (32 bit, little endian). krakenlcd: joined to the pixels, the transfer goes through and
   nothing is shown.
6. the pixels, R G B 0 for each, row by row: in 512-byte writes on the Kraken Z, 2 MB on the
   others (liquidctl's `bulk_buffer_size`)
7. `36 02`, then 50 ms
8. `38 01 04 <bucket>`

liquidctl and krakenlcd check byte 14 of the answers to 3 and 8; liquidctl logs a refusal and
goes on, and so does Libre Panel (once).

## The Kraken 2023 on firmware 2

liquidctl sends it a picture without buckets: `36 01 00 01 06`, the header with the kind `06`,
the pixels as RGB565 big endian (2 bytes each), `36 02`, each report followed by a read; the
first picture after opening twice ("some sort of framebuffer swapping?"). GIFs are not taken on
that firmware.

## The turn

The cooler turns the screens it draws itself, not uploaded pictures (krakenlcd): the turn of
`30 01`, in quarters clockwise, goes into the pixels (liquidctl's `rotate(rotation * -90)`).

## Not covered

- The Kraken Elite 2023 (`1e71:300c`): liquidctl lists it as "broken".
- GIFs and the other screens the firmware draws (liquidctl's `liquid` mode is the one used on
  closing).
