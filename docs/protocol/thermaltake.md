# Thermaltake 6" LCD

Applies to Thermaltake's 6" LCD of 1480×720, USB `264a:2347`, a HID device (InfoPanel names it
after the TOUGHLIQUID Ultra). Thermaltake's own program for it is TT RGB Plus. Driver:
`src/libre_panel/devices/thermaltake.py`.

Source, read in October 2026: [habibrehmansg/infopanel](https://github.com/habibrehmansg/infopanel)
(GPL-3.0), commit `8a3062d`: `InfoPanel/ThermaltakePanel/ThermaltakeHidDevice.cs`,
`ThermaltakePanelModelDatabase.cs` and `InfoPanel/Services/ThermaltakePanelDeviceTask.cs`. Its
first version (commit `f432334`, "Add Thermaltake 6" LCD panel support", with the note "achieves
~23fps at JPEG quality 85") sent packets captured from TT RGB Plus as they were; commit
`c41f481` built them instead, for ASRock's Phantom Gaming 360 LCD (`26ce:0a10`, 480×480), which
speaks the same protocol (from synqark's work on it). This is the only source found: nothing
here was seen on a panel by Libre Panel yet.

## Transport

HID reports of 1024 bytes, without a report id (HidSharp writes 1025, the id 0 in front). The
panel answers on the HID interface's IN endpoint.

## Commands

A command is text in the manner of an HTTP request, lines ending in CR LF:

```
POST realtimeDisplay 1
SeqNumber=101
Date=1792000000000
ContentType=json
ContentLength=15

{"enable":true}
```

`SeqNumber` counts up from 100 (TT RGB Plus's captures carry 112), `Date` is milliseconds since
1970. A command without a body ("POST conn 1") has neither `ContentType` nor `ContentLength`
(TT RGB Plus's own capture of it had `ContentType=json` and `ContentLength=240`, and no
`Date`). It is framed:

| Bytes | Content |
|---|---|
| 0–1 | `5A 00` |
| 2 | the length: the text's + 5 |
| 3– | the text |
| then | the checksum: the length and the text's bytes summed, the low byte |
| then | `5A 00` |

The rest of the report is zeros. The checksum matches the captures of "POST conn 1" (`07`) and
of "POST realtimeDisplay 1" with `{"enable":true}` (`08`); the capture for `{"enable":false}`
in InfoPanel's first version has `03`, where the sum gives `55` (that packet was made from the
other by hand, it seems; the later version computes it).

The panel answers in text; a command is taken where the answer holds `200` (InfoPanel reads the
answer 50 ms after the command).

| Command | Body | When |
|---|---|---|
| `POST conn 1` | none | opening |
| `POST realtimeDisplay 1` | `{"enable":true}` | opening, after conn |
| `POST realtimeDisplay 1` | `{"enable":false}` | closing: the screen goes back to the panel |
| `POST brightness 1` | `{"value":0-100}` | built by InfoPanel, never sent: not sent here either |

## A picture

A JPEG of 1480×720, in pieces of 1000 bytes, one a report after a header of 24 bytes:

| Bytes | Content |
|---|---|
| 0–3 | `5C 03 FD 00` (the last byte: the area, 0) |
| 4–5 | the number of pieces, big endian |
| 6–7 | this piece's number, from 0, big endian |
| 8 | `01` |
| 9–23 | zeros |
| 24–1023 | the piece; the last filled up with zeros |

No length is sent: the JPEG's end marker ends it. The panel does not answer pictures.
InfoPanel streams at its frame rate without a pause; whether the panel keeps a picture without
new ones is not known, so Libre Panel sends the last one again after a second without one. The
brightness goes into the pixels, as InfoPanel does it.
