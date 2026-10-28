# udisp USB displays (Waveshare's USB display image)

Applies to USB displays that call themselves "udisp" in their product string: Waveshare's USB
monitors with the card image from Waveshare's wiki that makes them a USB display (seen on the
2.8" round one, #21:
`303a:2986`, "t113udisp1_R480x480_Ejpg5_Ergb16_Ergb32", an Allwinner T113 with Linux 5.4.61),
and the ESP32 displays the protocol was first made for. Driver:
`src/libre_panel/devices/udisp.py`.

Source, read in October 2026: [chuanjinpang/win10_idd_xfz1986_usb_graphic_driver_display]
(https://github.com/chuanjinpang/win10_idd_xfz1986_usb_graphic_driver_display) (GPL-2.0), commit
`c2de0f5`: the Linux driver's `src/inc/enc_base.h` (the frame header), `src/enc_base.c`,
`src/enc_raw_rgb.c`, `src/enc_jpg.c` and `src/usb_transfer.c` (the endpoint, the product string's
fields, a picture as one transfer), and the ESP32 firmware's `main/app_vendor.c` (how a display
reads it). The descriptors of the 2.8" come from its owner's `lsusb -v` (#21); what draws the
pictures on it, from the card image Waveshare's wiki offers: `showJPG`, which reads them from
`/proc/udisp/xfz1986`, decodes them with the T113's JPEG decoder and writes them to `/dev/fb0`.
Libre Panel has seen one display so far: the 2.8" took raw RGB565 pictures, 58 a second, and
stayed black (#30).

## Transport

USB 2.0 high speed, vendor-specific class, one interface with a bulk OUT endpoint (`0x01`, 512
bytes) and a bulk IN endpoint (`0x81`, not used). `303a:2986` is the display with audio and
touch beside it in the ESP32 projects, `303a:2987` the display alone. The 2.8" has two
configurations, named as Linux's test gadgets are ("loop input to output", "source and sink
data"); the first is the one in use.

## The product string

Fields between underscores, after the device's own name:

| Field | Meaning |
|---|---|
| `R480x480` | width × height of the picture |
| `Ejpg5` | takes JPEG; the quality its driver uses, 1 to 10 |
| `Ergb16` | takes RGB565 |
| `Ergb32` | takes RGB888 |
| `Fps30` | at most this many pictures a second (optional) |
| `Bl20` | a JPEG of at most this many KB (optional; else 8 MB) |

The kind of picture named first is the one the display is sent: the drivers read the `E`
fields in order and keep the first ("first is high prio", `usb_transfer.c`; JPEG at quality 5
where none is named), and send no other. What is named after it may never be drawn: the 2.8"
names raw pixels after JPEG, but `showJPG` on its card decodes JPEG only, and raw pixels left it
black (#30).

## A picture

One bulk transfer: a header of 16 bytes, little-endian, then the payload.

| Bytes | Field |
|---|---|
| 0–1 | CRC-16 of the payload (the drivers send 0) |
| 2 | type: 0 RGB565, 1 RGB888, 2 YUV420, 3 JPEG |
| 3 | command (0) |
| 4–5, 6–7 | x, y of the picture's corner (0, 0 for a whole picture) |
| 8–9, 10–11 | width, height |
| 12–15 | bits 0–9: a frame number; bits 10–31: the payload's length in bytes |

A transfer ends with a packet shorter than the endpoint's 512 bytes. After a picture of whole
packets, which has none, the Windows driver sends a header of type `0xFF` (x and y 0, 1×1,
nothing in it) to end it; a display that needs no end passes it over as a command it does not
know, as the ESP32 firmware does. Libre Panel sends it so too.

RGB565 is 16 bits a pixel, little-endian, red in the top 5 bits; RGB888 is three bytes a
pixel, red first (as the xfz1986 drivers pack them from an XRGB8888 framebuffer). A JPEG is
baseline with its colours at every pixel (4:4:4), as the drivers' TinyJPEG writes it. Libre
Panel streams whole pictures as the kind the display names first, at the frames a second of a
TURZX panel in video mode (`[video] fps`, 30 by default; fewer where the product string says
`Fps`): a JPEG at quality 90 (lower only where `Bl` limits its size); raw pixels only for a
display that names them first (RGB565 at 480×480 is 450 KB, 13.5 MB a second at 30, within USB
2.0's high speed). The last picture goes again after a second without one. The display takes the next transfer only once it has the last, so the time a
picture takes to encode and send is what it costs the display: Libre Panel times the first 24
and drives the stream no faster than they went.

Waveshare's 2.8" (`t113udisp` in its product string) shows a picture turned by 180°: its top
lands at the edge away from the cable, upside down as the display is mounted (#33; CluView
found the same). Libre Panel sends it turned, so that it is upright with *Upside down* off; the
ESP32 displays get it as it is.

No command for the backlight is known: Libre Panel's brightness goes into the picture, and a
change of it sends the last picture again at once (the panel test changes it and draws nothing
new). At 100 % the picture goes as it is: how bright that looks is the display's own backlight.
