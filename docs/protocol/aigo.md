# AIGO case screens

Applies to the screen of AIGO's Xingcanchen case (爱国者星璨辰), 1920×462, USB `1d6b:0103`
("Aigo USB Device" of "Aigo Inc."), a HID device (interface 0, usage page `0xFF00`, interrupt
OUT `01` and IN `81` of 1024 bytes; Windows binds its own HID driver, its output reports are
1025 bytes with the report id 0 in front). Driver: `rust/devices/src/aigo.rs`; on Windows the
reports go through Windows' HID driver (`rust/devices/src/win_hid.rs`).

Sources, read in November 2026: its owner's captures of AIGO's program and its logs
([issue #68](https://github.com/syntensa/libre-panel/issues/68), firmware V1.0.11), and
[Savathun/LanScreen](https://github.com/Savathun/LanScreen) (MIT): `docs/PROTOCOL.md` and
`src/Core.cs`, written for the Xingcanlan (星璨岚, `1d6b:0172`, 1480×720) of the same family and
run by the owner on the Xingcanchen with its ids and size changed. The protocol is close to
[Thermaltake's LCD](thermaltake.md). Nothing here was seen on a screen by Libre Panel yet.

## Commands

Text in the manner of an HTTP request, lines ending in CR LF, as Thermaltake's LCD takes them:

```
POST realtimeDisplay 1
SeqNumber=3
Date=1795600000123
ContentType=json
ContentLength=15

{"enable":true}
```

`ContentLength` counts the body's bytes. Framed: the length (the text's + 5) in two bytes
big-endian, the text, and a checksum, the sum of the length's bytes and the text's (mod 256);
each `5A` among them is written `5B 01` and each `5B` `5B 02`; a `5A` before and after. The
report is filled up with zeros. The screen answers `1 200` in its first line where it did it,
with `AckNumber` the command's `SeqNumber` + 1, and for `conn` a JSON body (its `sn`, and
whether `realtimeDisplay` is on).

| When | Command | Body |
|---|---|---|
| opening (a screen asleep takes nothing before it) | `POST power 1` | `{"event":"resume"}` |
| then, after 100 ms | `POST conn 1` | |
| | `STATE all 1` | `{"heartbeat":1}` |
| | `POST realtimeDisplay 1` | `{"enable":true}` |
| every 5 seconds, pictures or not | `STATE all 1` | `{"heartbeat":1}` |
| closing: the screen shows its own again | `POST realtimeDisplay 1` | `{"enable":false}` |

AIGO's program sends `conn` before `power`; LanScreen sends `power` first, as a screen asleep
may refuse `conn`. A screen that shows the pictures it is sent already may refuse
`realtimeDisplay` again: `conn` says whether it does.

## Pictures

A JPEG of 1920×462 (AIGO's program writes quality 85, at most 512 KiB) in pieces of 1000
bytes, each a report of 1024 bytes after a header of 24, not escaped:

| Offset | Bytes | |
|---|---|---|
| 0 | 1 | `5C` |
| 1 | 2 | the piece's length + 21, big-endian |
| 3 | 1 | `34` (AIGO's program varies it; LanScreen writes `34`) |
| 4 | 2 | the count of pieces, big-endian |
| 6 | 2 | the piece's number from 0, big-endian |
| 8 | 1 | `01` |
| 9 | 15 | zeros |
| 24 | 1000 | the piece, the last filled up with zeros |

AIGO's program sends the same picture about 21 times a second; Libre Panel sends one when it
changed and keeps the link with the heartbeat (LanScreen does the same and the owner saw the
screen keep the link).

Not known yet: a brightness command (the brightness goes into the pixels), and what the
fields `space` and `background` of the screen's answers are for.
