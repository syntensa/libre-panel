# TURZX-USB-Panel — Wire-Protokoll (Stand 2026-09-02)

## Kurzfassung
Das Protokoll ist **gelöst — ohne USB-Capture**. `turing-smart-screen-python`
(Modul `library/lcd/lcd_comm_turing_usb.py`) und `TURZX.exe` implementieren
**dasselbe** Kommandoprotokoll. Das wurde nicht angenommen, sondern durch
IL-Byte-Vergleich in `TURZX.exe.original` verifiziert (Belege unten).

**Zusätzlich:** die Bibliothek kann inzwischen **H.264-Video über USB streamen**
(Kommandos 17 / 121 / 122 / 123). Die Notiz „no video or storage support" in der
HANDOFF ist veraltet. Damit entfällt der Grund für Weg B — der Video-Pfad ist
Teil von Weg A.

**Und die Geräte-ID passt ebenfalls** — die Angabe „VID 1A86 / PID AD10–AD13" aus
der HANDOFF war falsch. Das angeschlossene Panel meldet sich als
`USB\VID_1CBE&PID_0092`, und `0x1CBE/0x0092` steht wörtlich in der Gerätetabelle
der Bibliothek. Es ist also **kein Transport-Adapter nötig**; siehe
„Geräte-Identität (verifiziert)".

## Beweislage: TURZX.exe ↔ turing-smart-screen-python

Referenz-Python (`lcd_comm_turing_usb.py`):

    def build_command_packet_header(a0):
        packet = bytearray(500)
        packet[0] = a0
        packet[2] = 0x1A
        packet[3] = 0x6D
        packet[4:8] = struct.pack('<I', ms_seit_mitternacht)

    def encrypt_command_packet(data):
        encrypted = DES-CBC(key=slv3tuzx, iv=slv3tuzx).encrypt(pad8(data))
        final = bytearray(512); final[:len(encrypted)] = encrypted
        final[510] = 161; final[511] = 26

Gefunden in `TURZX.exe.original` (Dateioffsets, roher IL):

| Offset    | IL-Bytes                        | Bedeutung                              |
|-----------|---------------------------------|----------------------------------------|
| 0x385D0   | `20 F4 01 00 00 8D ...`         | `newarr byte[500]`                     |
| 0x385DD   | `06 16 03 9C`                   | `packet[0] = a0` (a0 kam als `ldarg.3`)|
| 0x385E1   | `06 18 1F 1A 9C`                | `packet[2] = 0x1A`                     |
| 0x385E6   | `06 19 1F 6D 9C`                | `packet[3] = 0x6D`                     |
| 0x385F4   | `07 16 06 1A 1A 28 1E 01 00 0A` | `Array.Copy(ts, 0, packet, 4, 4)`      |
| 0x386AF   | `1E 8D 46 00 00 01 25 D0 63 07 00 04 28 2A…` | `byte[8]`-Feld per `InitializeArray` = DES-Schlüssel |
| 0x386BE   | `20 00 02 00 00 8D ...`         | `newarr byte[512]`                     |
| 0x386DB   | `07 20 FE 01 00 00 20 A1 00 00 00 9C` | `final[510] = 161`               |
| 0x386E7   | `07 20 FF 01 00 00 1F 1A 9C`    | `final[511] = 26`                      |
| 37345100  | ASCII `slv3tuzx`                | DES-Schlüssel, genau 1× in der Datei   |

H.264-Chunk-Aushandlung, ebenfalls 1:1 (ab 0x369EC):

    02                  ldarg.0
    20 00 18 03 00      ldc.i4 202752         ; Default-Chunkgröße
    28 98 02 00 06      call   set_chunkSize
    02 1F 11            ldarg.0 / ldc.i4.s 17 ; CMD_GET_H264_CHUNK_SIZE
    28 BB 02 00 06      call   sendCommand
    06 1E 91 1F 18 62         ; resp[8]  << 24
    06 1F 09 91 1F 10 62 58   ; resp[9]  << 16
    06 1F 0A 91 1E 62 58      ; resp[10] <<  8
    06 1F 0B 91 58            ; resp[11]

= `int.from_bytes(resp[8:12], "big")` — identisch zur Python-Implementierung.

## Protokoll (aus der Referenzimplementierung)

**Kommandopaket:** 500 Byte Klartext → DES-CBC (Key = IV = `slv3tuzx`,
Null-Padding auf Vielfache von 8) → in 512-Byte-Puffer, Trailer `A1 1A` auf
Offset 510/511. Danach folgt bei Datenkommandos direkt die **unverschlüsselte**
Nutzlast (PNG/JPEG/H.264) im selben Bulk-OUT-Write.

Klartextlayout: `[0]` = Kommando-ID, `[2..3]` = `1A 6D` (Magic),
`[4..7]` = Millisekunden seit Mitternacht (LE), `[8..]` = Argumente.
Größenangaben in `[8..11]` sind **Big-Endian**.

**Antwort:** 512 Byte Bulk-IN. OK, wenn `resp[1] == 0xC8` oder `resp[8] == 0xC8`.

**Kommando-IDs**

| ID  | Zweck                        | Argumente                                    |
|-----|------------------------------|----------------------------------------------|
| 10  | Sync / Handshake             | —                                            |
| 11  | Neustart Gerät               | — (Lib deaktiviert es; wir nutzen es, s. u.) |
| 13  | (Teil der Video-Init)        | —                                            |
| 14  | Helligkeit                   | `[8]` = 0…102                                |
| 15  | Framerate                    | `[8]` = fps (25)                             |
| 17  | H.264-Chunkgröße abfragen    | Antwort `[8..11]` BE, Default 202752         |
| 41  | (Teil der Video-Init)        | —                                            |
| 100 | Speicher-Info                | Antwort `[8..19]` = total/used/valid (LE)    |
| 101 | JPEG-Vollbild                | `[8..11]` = Länge BE, danach JPEG-Bytes      |
| 102 | PNG-Vollbild                 | `[8..11]` = Länge BE, danach PNG-Bytes       |
| 111 | (Teil der Video-Init)        | —                                            |
| 112 | (Teil der Video-Init)        | —                                            |
| 121 | H.264-Chunk abspielen        | `[8..11]` = Länge BE, `[12]` = 1 bei letztem |
| 122 | Streamstatus / Queue-Tiefe   | Antwort `[8]` = Füllstand                    |
| 123 | Stream stoppen               | —                                            |
| 125 | Einstellungen speichern      | `[8..13]` = Helligkeit, Startmodus, res, Rotation, Sleep, Offline |

**Video-Init-Sequenz** (aus `send_video`): 111 → 112 → 13 → 14(32) → 41 →
102(Clear-PNG) → 15(25 fps) → 17(Chunkgröße) → dann Schleife 121 mit
Flusskontrolle über 122 (bei `resp[8] > 3` warten).

**Bildpfad:** PNG bevorzugt; überschreitet es das Limit, JPEG mit absteigender
Qualität. Alle Bilder gehen im Hochformat 480×1920 raus.

**Transport in der Lib:** `usb.core.find(idVendor, idProduct)` →
`set_configuration()` → Interface 0 → erster OUT- und erster IN-Endpoint,
Bulk-Write mit 2000 ms Timeout, danach 512-Byte-Read + Flush.

## Geräte-Identität (verifiziert am angeschlossenen Gerät)

Abgefragt über die Windows-PnP-Datenbank, Gerät war zu dem Zeitpunkt verbunden
und im Zustand `OK`:

    InstanceId    USB\VID_1CBE&PID_0092\<SERIENNUMMER>
    HardwareIds   USB\VID_1CBE&PID_0092&REV_0000
    BusReported   TURZX1.0
    Klasse        USBDevice (kein Composite — DEVPKEY_Device_Children ist leer)
    CompatibleIds USB\MS_COMP_WINUSB | USB\Class_FF&SubClass_00&Prot_00
    Service       WINUSB   (winusb.inf, Microsoft, 10.0.26100.8972)
    IfaceGUID     {88BAE032-5A81-49f0-BC3D-A4FF138216D6}
    Parent        USB\VID_174C&PID_2074  (ASMedia-USB-Hub)

Daraus folgt Punkt für Punkt:

* **VID `0x1CBE`, PID `0x0092`** — nicht `1A86/AD1x`. Genau dieser Eintrag steht
  in `PRODUCT_ID` der Bibliothek (`0x0092: Turing 9.2"`). Die Seriennummer
  (`<SERIENNUMMER>`, entfernt) deckt sich mit der in der HANDOFF notierten.
* **Woher kam `1A86/AD10–AD13`?** Aus `Driver/TURZX_display_driver.inf` im
  Installationsordner. Das ist ein **IddCx-Indirect-Display-Treiber**
  (`UmdfExtensions = IddCx0102`, `Class = Display`, Upper-Filter `IndirectKmd`)
  für eine **andere** TURZX-Produktvariante, die sich als virtueller
  Zweitmonitor meldet. Für unser Panel ist er nicht installiert und irrelevant.
* **WinUSB ist bereits gebunden**, und zwar über den MS-OS-Descriptor
  (`MS_COMP_WINUSB`) — nicht über eine mitgelieferte INF. Kein Zadig, kein
  Treiberwechsel nötig; libusb kann das Gerät über sein WinUSB-Backend öffnen.
* **Kein Composite-Gerät**, Class FF vendor-specific → es gibt genau ein
  Interface. Die Annahme der Bibliothek (Interface 0, erster Bulk-OUT/-IN)
  greift damit.

**Fazit: kein Transport-Adapter nötig.** `LcdCommTuringUSB` spricht das Gerät
unverändert an.

Bestätigung, dass das Upstream-Projekt genau diese Familie meint —
`requirements.txt`:

    pyusb~=1.3.1         # For TURZX USB models: communicate directly through USB
    pycryptodome~=3.23.0 # For TURZX USB models: decrypt/encrypt frames using DES

### Eine Stelle, die wir trotzdem patchen müssen: 462 vs. 480

`PRODUCT_ID` mappt `0x0092 → (462, 1920)`, und `LcdCommTuringUSB.__init__`
überschreibt damit die Größe. Upstream widerspricht sich hier selbst —
`library/display.py:78`:

    elif ... == '9.2"':
        return 480, 1920 # 9.2" displays are 1920x462 but using 1920x480 to be compatible with 8.8" themes

TURZX selbst arbeitet ebenfalls mit 480×1920: der Theme-/Video-Schlüssel ist
`4801920`, und die Dateien darin heißen `8.8*.mp4` — dieselbe 8.8"-Geometrie.
Unser Renderer nimmt daher **480×1920**; der 462er-Eintrag wird im Fork auf
`(480, 1920)` gesetzt. Ob das Panel die letzten 18 Spalten physisch zeigt oder
abschneidet, ist am Gerät zu prüfen (Testbild mit Randmarkierung).

## Am Gerät verifiziert (Lauf vom 2026-09-02)

Deskriptoren, `tools/panel_probe.py enum`:

    1cbe:0092  Hersteller TURZX  Produkt TURZX1.0  Seriennr. <SERIENNUMMER>
    USB 2.0, EP0 64 B, Konfiguration 1: 1 Interface, max 120 mA
    Interface 0 Alt 0, Class ff/00/00, 2 Endpoints
      EP 0x81  IN   Bulk  wMaxPacketSize 512  bInterval 0
      EP 0x01  OUT  Bulk  wMaxPacketSize 512  bInterval 0

Genau ein Interface, je ein Bulk-OUT und Bulk-IN mit 512 B — die Annahme der
Bibliothek (Interface 0, erster OUT/IN) trifft zu. High-Speed, also brutto
480 Mbit/s, praktisch grob 30–40 MB/s.

Handshake und Abfragen, `tools/panel_probe.py sync`:

| Kommando | Antwort | Deutung |
|---|---|---|
| 10 Sync | `0a c8 …` + ASCII `turzx_00…` ab `resp[8]` | OK, plus Gerätekennung |
| 17 H.264-Chunkgröße | `11 c8 …`, Feld = **0** | Gerät handelt nichts aus → Default 202752 benutzen |
| 100 Speicher-Info | `64 c8 …`, alle Felder **0** | **kein Kartenspeicher vorhanden** |
| 102 PNG-Vollbild, 34554 B | `66 c8 …` | Vollframe angenommen |

`resp[0]` ist das Echo der Kommando-ID, `resp[1] = 0xC8` das OK — beide
Quittungsformen aus `_resp_ok` treffen hier zu.

Bestätigt damit: Punkt 1 und 3 der offenen Liste sind erledigt, der Bildpfad
funktioniert. Dass Cmd 17 und Cmd 100 Nullen liefern, passt zusammen — dieses
Modell hat keinen internen Speicher, es ist ein reines Streaming-Panel.

## Bildformat: RGBA ist Pflicht (am Gerät entschieden)

Am 2026-09-02 wurden sechs Varianten desselben Testmusters ans Panel geschickt
(`tools/panel_probe.py diag`). Gewonnen hat **Variante C**:

| | Geometrie | Pixelformat | Format | Ergebnis |
|---|---|---|---|---|
| A | 1920x480 quer | RGBA | PNG | falsch |
| B | 1920x480 quer | RGB | PNG | falsch |
| **C** | **480x1920 hochkant, ROTATE_270** | **RGBA** | **PNG** | **korrekt** |
| D | 480x1920 hochkant, ROTATE_90 | RGBA | PNG | falsch |
| E | 1920x480 quer | RGB | JPEG | falsch |
| F | 480x1920 hochkant, ROTATE_270 | RGB | JPEG | falsch |

Daraus folgt:

* **Der Framebuffer ist 480x1920 hochkant**, wie angenommen. Die Design-Flaeche
  fuer SPUR II ist 1920x480 quer; gedreht wird mit `ROTATE_270` (im Uhrzeigersinn
  90 Grad) unmittelbar vor dem Senden — `to_wire()` in `tools/panel_probe.py`.
* **Das Panel verlangt PNG mit Farbtyp 6 (RGBA, 8 Bit).** Mit Farbtyp 2 (RGB)
  verrechnet sich sein Decoder um ein Byte pro Pixel: das Bild erscheint
  vierfach gekachelt mit einem toten Streifen am Rand. Das war der einzige
  Fehler im ersten Anlauf — Geometrie und Drehung stimmten von Anfang an.
  Passt zum eingebauten Clear-Bild der Referenzbibliothek, dessen PNG-Header
  `00 00 01 e0  00 00 07 80  08 06` traegt: 480 x 1920, Bittiefe 8, Farbtyp 6.
* **JPEG (Cmd 101) funktioniert auf diesem Geraet nicht.** Gezielt
  nachgeprueft: Variante F wird vom Geraet mit `65 c8` quittiert — also formal
  angenommen — aber nicht korrekt angezeigt. Da JPEG keinen Alphakanal kennt und
  PNG mit Farbtyp 2 denselben Fehler zeigt, erwartet der Bildpfad offenbar hart
  4 Byte pro Pixel. **Damit ist PNG/RGBA der einzige Bildweg.**
  Merke fuer die Fehlersuche: eine `0xC8`-Quittung heisst nur, dass das Kommando
  angenommen wurde — nicht, dass das Bild richtig dargestellt wird.

## 462 gegen 480: entschieden zugunsten von 480

Im gewinnenden Testbild ist **keines der vier Randbaender abgeschnitten** —
weiss links, gelb rechts, rot oben, blau unten sind alle sichtbar. Das Panel
zeigt die vollen 480 Zeilen der Design-Flaeche. Der Eintrag
`0x0092: (462, 1920)` in `PRODUCT_ID` der Bibliothek ist fuer dieses Geraet
falsch; im Fork gilt `(480, 1920)`.

## Bildrate des Bitmap-Pfads (gemessen, CPU-Seite)

`tools/panel_bench.py` rendert einen repraesentativen Dashboard-Frame
(Verlauf, zwei gefuellte Graphen, sechs Balken, Glow, 16 Textausgaben),
dreht ihn, kodiert RGBA-PNG und misst jede Stufe einzeln.

Erster Lauf, naiv implementiert — **21.6 fps**, zu langsam:

| Stufe | render | dreh+RGBA | PNG | gesamt | fps | Byte/Frame |
|---|---|---|---|---|---|---|
| 0 | 33.2 ms | 1.7 ms | 16.5 ms | 51.3 ms | 19.5 | 3 689 590 |
| 1 | 31.9 ms | 1.7 ms | 12.8 ms | 46.3 ms | 21.6 | 232 163 |
| 3 | 31.8 ms | 1.7 ms | 14.4 ms | 47.9 ms | 20.9 | 139 246 |
| 6 | 31.6 ms | 1.7 ms | 19.5 ms | 52.9 ms | 18.9 | 121 715 |

Einzelmessung zeigte: nicht PNG ist teuer, sondern der Glow.

| Posten | Kosten |
|---|---|
| GaussianBlur(9) auf dem Vollbild | 14.9 ms |
| Screen-Blend in numpy | 11.1 ms |
| Verlauf (numpy) | 3.8 ms |
| 2 Graphen, 60 Rasterlinien, 16 Texte | zusammen ~2.2 ms |

Zwei Aenderungen, beide ohne sichtbaren Qualitaetsverlust:

1. **Blur auf einem Viertel der Aufloesung** statt auf dem Vollbild —
   5.3 statt 14.9 ms. Glow ist niederfrequent, der Unterschied ist unsichtbar.
2. **`ImageChops.screen` statt numpy** — 3.4 statt 11.1 ms, dieselbe Rechnung
   in C statt in Python.
3. Verlauf und Raster einmal bauen und nur noch kopieren — 0.45 statt 4.3 ms.

Zweiter Lauf, **41.8 fps**:

| Stufe | render | dreh+RGBA | PNG | gesamt | fps | Byte/Frame |
|---|---|---|---|---|---|---|
| 1 | 11.9 ms | 1.6 ms | 11.7 ms | 25.2 ms | 39.7 | 178 183 |
| 2 | 10.7 ms | 1.6 ms | 11.6 ms | 23.9 ms | **41.8** | 118 799 |
| 3 | 10.7 ms | 1.6 ms | 12.2 ms | 24.5 ms | 40.8 | 108 890 |

Kompressionsstufe **2** ist der beste Kompromiss: praktisch so schnell wie
Stufe 1, aber ein Drittel weniger Daten. Stufe 0 scheidet aus — 3.7 MB pro
Frame liegen weit ueber dem 1-MiB-Limit.

## Zero-Length-Packet: die Quittung braucht 1024, nicht 512

**Das wichtigste Transportdetail, und es hat einen halben Abend gekostet.**

Die Quittung ist exakt **512 Byte** gross, und `wMaxPacketSize` des Bulk-IN ist
ebenfalls **512**. USB verlangt in diesem Fall ein **Zero-Length-Packet** als
Signal fuer das Transferende. Fordert der Host genau 512 Byte an, holt der erste
Read die Quittung, das Nullpaket bleibt in der Pipe stehen — und der naechste
Read liefert es als leere Antwort. Ab da ist alles um eine Antwort versetzt.

So sah der Fehler aus (Init-Sequenz von `panel_video.py`):

    Cmd 111 -> leer
    Cmd 112 -> 6f c8      0x6f = 111   Antwort auf das VORIGE Kommando
    Cmd 13  -> leer
    Cmd 14  -> 70 c8      0x70 = 112
    Cmd 102 -> leer
    Cmd 15  -> 0d c8      0x0d = 13

Und die Folge war nicht nur kosmetisch: die IN-Pipe lief voll, das Geraet
stallte den Bulk-OUT, und der Live-Stream brach mit
`[Errno 10060] Operation timed out` ab.

**Die Loesung ist eine Zeile:** mit `READ_LEN = 1024` lesen statt mit 512. Dann
beendet das kurze Paket den Transfer selbst, die Quittung kommt in einem Stueck
und die Pipe bleibt synchron. Kostet nichts.

Der `read_flush` der Referenzbibliothek loeste dasselbe Problem — nur teuer, mit
einem 100-ms-Timeout pro Frame. Er wurde hier zuerst als reine Zeitverschwendung
entfernt; erst der Absturz zeigte, dass er nebenbei eine echte Aufgabe hatte.

**Absicherung eingebaut:** `Panel.cmd` prueft jetzt, ob `resp[0]` die gesendete
Kommando-ID zurueckgibt, und meldet sonst `[!] Quittung gehoert zu Cmd X`.
`Panel.resync()` raeumt beim Oeffnen Reste aus der Pipe. Merke: das Geraet
echot in `resp[0]` immer die Kommando-ID — wer das prueft, sieht eine
verschobene Pipe sofort.

### Was das fuer die bisherigen Messungen bedeutet

Der **erste** Benchmark lief noch mit dem Flush der Referenz, also mit
**synchroner** Pipe: 193.9 ms USB pro Frame, davon rund 100 ms Flush-Timeout —
bleiben rund **94 ms echte Zeit pro Vollframe**, also gut 10 fps.

Die Zerlegung in `panel_timing.py` lief dagegen mit **versetzter** Pipe. Die
dortigen *Lesezeiten* von 0.1 ms sind wertlos (sie holten alte Nullpakete). Die
*Schreibzeiten* sind es nicht: 87.1 ms fuer den komplexen Frame decken sich mit
den 94 ms aus dem synchronen Lauf. **Die Aussage, dass das Panel den Bulk-OUT
bremst und der Bitmap-Pfad bei gut 10 fps deckelt, steht damit auf zwei
unabhaengigen Messungen** — die Entscheidung fuer H.264 bleibt richtig.

## Latenz pro Frame: nicht die Bandbreite

Benchmark mit angeschlossenem Panel, 30 Frames je Stufe:

| Stufe | render | dreh+RGBA | PNG | USB | gesamt | fps | Byte/Frame |
|---|---|---|---|---|---|---|---|
| 1 | 11.5 ms | 1.5 ms | 11.7 ms | **193.9 ms** | 218.6 ms | 4.6 | 177 374 |
| 2 | 11.0 ms | 1.5 ms | 12.0 ms | **200.3 ms** | 224.8 ms | 4.4 | 118 308 |
| 3 | 11.0 ms | 1.5 ms | 12.5 ms | **199.3 ms** | 224.4 ms | 4.5 | 108 375 |

Entscheidend ist, was diese Tabelle **nicht** zeigt: 177 KB und 108 KB kosten
gleich viel. Die Zeit haengt nicht von der Datenmenge ab.

**Rund die Haelfte war selbst verursacht.** `Panel.xfer` hatte den Flush der
Referenzbibliothek uebernommen:

    for _ in range(5):
        try:    ep_in.read(512, timeout=100)
        except: break

Hat das Geraet nichts mehr zu senden — der Normalfall —, wartet dieser Aufruf
die vollen 100 ms ab, bevor er als Timeout zurueckkommt und die Schleife
abbricht. Bei den 1–2 fps, fuer die die Referenz gebaut ist, faellt das nicht
auf; bei 25 fps ist es die Haelfte des Budgets. **Flush ist jetzt
standardmaessig aus.**

### Die Zerlegung (`tools/panel_timing.py`)

**A/B — Leerkommando (Cmd 122, 512 Byte, keine Nutzlast):**

| | gesamt | schreiben | lesen |
|---|---|---|---|
| ohne Flush | **0.1 ms** | 0.1 ms | 0.0 ms |
| mit Flush | 108.2 ms | 0.1 ms | 0.1 ms |

Die Protokolllatenz ist praktisch null, der Flush kostete exakt die erwarteten
rund 108 ms.

**C — Nutzlast-Sweep, Schreiben und Lesen getrennt:**

| Inhalt | Bytes | schreiben | lesen |
|---|---|---|---|
| einfarbig | 8 044 | **0.3 ms** | 0.1 ms |
| Verlauf | 8 081 | **60.4 ms** | 0.1 ms |
| komplex | 117 466 | **87.1 ms** | 0.1 ms |

Das ist der Kern. Einfarbig und Verlauf sind praktisch gleich gross
(8 044 gegen 8 081 Byte) und unterscheiden sich um **Faktor 200** in der Zeit.
Weder Bandbreite noch Protokoll — und die Quittung kommt immer in 0.1 ms.

**Was tatsaechlich passiert:** das Panel bremst den **Bulk-OUT**, bis es den
vorigen Vollframe verarbeitet hat. Der Schreibaufruf blockiert, nicht der
Lesevorgang. Ein einfarbiges Bild hat offenbar einen Schnellweg; jeder echte
Vollbildinhalt kostet rund **60 ms Grundlast plus etwa 0.25 ms pro KB** — die
27 ms Differenz zwischen Verlauf und komplex auf 109 KB mehr entsprechen gut
4 MB/s tatsaechlichem Transfer.

**D — Pipelining:** brach bei K=1 mit einem Schreib-Timeout ab. Kein sauberes
Ergebnis; der Fehler kann auch daher kommen, dass das Geraet nach der
ungewohnten Sequenz kurz stand. Fuer die Entscheidung ohne Belang — selbst
perfektes Pipelining braechte uns nicht von 16 auf 25 fps.

## Entscheidung: H.264 statt Bitmap

**Der Bitmap-Pfad deckelt bei 11–16 fps**, und zwar geraeteseitig, unabhaengig
davon, wie schnell wir rendern. Selbst mit beliebig kleinem Frame waeren rund
16 fps das Ende. Das erklaert nebenbei, warum die Originalsoftware bei 1 Hz
stehenbleibt.

Der Onboard-H.264-Decoder umgeht genau diesen Flaschenhals — dafuer ist er da.
Weg B ist damit **nicht Fallback, sondern der vorgesehene Weg fuer bewegte
Bilder**. Gekostet hat die Erkenntnis nichts: die Kommandokette stand bereits,
und ein USB-Mitschnitt war nie noetig.

Encoder-Kette ohne Panel gemessen (`ffmpeg.exe` aus dem Installationsordner,
libx264, Constrained Baseline, ultrafast + zerolatency, 480x1920):

    75 Frames in 1.60 s = 47.0 fps
      rendern + drehen    : 13.3 ms/Frame
      in ffmpeg schreiben :  1.3 ms/Frame
      H.264               : 14 413 Byte/Frame = 2.88 Mbit/s

**47 fps bei 2.88 Mbit/s** — 360 KB/s ans Panel statt 2 975 KB/s im
Bitmap-Pfad, Faktor 8 weniger, bei gemessenen rund 4 MB/s Transferrate also
reichlich Luft.

Werkzeug `tools/panel_video.py`:

    play <datei.h264>   fertigen Annex-B-Strom abspielen — trennt den Transport
                        vom eigenen Encoder, der erste Beweis
    live                rendern -> ffmpeg -> Panel, in Echtzeit

Encoder-Parameter passend zu den Referenzvideos des Geraets: Constrained
Baseline, kein B-Frame, kein CABAC, Keyframe jede Sekunde, `repeat-headers=1`
damit das Panel auch mitten im Strom aufsetzen kann. `h264_amf` (AMD-GPU)
ist als Alternative eingebaut, wird aber nicht gebraucht.

## Werkzeug-Stand auf diesem Rechner

Python 3.13.15 ist installiert, venv unter `panel-project/.venv` mit pyusb,
pycryptodome, Pillow, numpy, pyserial und `libusb-package` — letzteres bringt
libusb-1.0 mit, es muss nichts nach System32 kopiert werden.

Werkzeuge in `tools/`:

| Datei | Zweck |
|---|---|
| `panel_probe.py` | Kommandoschicht: `enum` `sync` `testcard` `image` `brightness` `clear` `diag` |
| `panel_bench.py` | Bildrate des Bitmap-Pfads, Stufen einzeln gemessen |
| `panel_timing.py` | Latenz zerlegen: Grundlatenz, Nutzlast-Sweep, Pipelining |
| `panel_video.py` | H.264: `play <datei.h264>` und `live` |
| `panel_diagnose.py` | wo bleibt die Zeit: USB, Warteschlange, ffmpeg getrennt |
| `panel_neustart.py` | Cmd 11, wenn der Dekoder hängt |

Stand später: Cmd 125 (Einstellungen speichern) schickt inzwischen jede
Video-Init in `panel_video.py`, Cmd 11 (Neustart) das Werkzeug oben.
Speicherschreibbefehle sind weiterhin in keinem davon implementiert, und
Cmd 12 in keinem — mit Absicht.

## Noch zu klären (braucht das Gerät)

1. **Ob der H.264-Pfad am Geraet traegt** — erst mit einer Referenzdatei
   (`panel_video.py play`), dann live encodiert (`panel_video.py live`).
2. **Ob der Schwarzwert des Panels die dunkle Basisrampe von SPUR II traegt.**
   Auf den Fotos wirkt der Hintergrund deutlich aufgehellt; die vier Ebenen
   `#070A0E` bis `#1D2531` koennten ununterscheidbar zusammenfallen. Betrifft
   das Design, nicht das Protokoll.

## Lizenz-Hinweis
`turing-smart-screen-python` steht unter **GPLv3**. Übernommener Code macht
unseren Renderer GPLv3-pflichtig. Die Protokollfakten oben sind unabhängig aus
`TURZX.exe` verifiziert; eine saubere Neuimplementierung wäre also möglich,
falls die Lizenz je stört. Für den privaten Eigengebrauch spielt das keine Rolle.

## Die Firmware selbst — vollständige Kommandotabelle

Bis hierher war die Tabelle aus Beobachtung und aus dem IL von `TURZX.exe`
zusammengesetzt, mit Lücken. Sie lässt sich vollständig belegen: `fw/turzx_88inch_0015`
ist **kein Flash-Abbild**, sondern die Anwendung, die auf dem Panel läuft — ein
MIPS32-ELF für Ingenic-SoC unter Linux, dynamisch gelinkt und **unstrippt**, also
mit Symbol- und Debugtabelle. Beide Dateien im Ordner sind byte-identisch
(MD5 `9609176c…`), es gibt keine getrennte 8.8"-/8"-Firmware.

Analysewerkzeug: `tools/fwtools/mips.py` (rein lesend, capstone).

`main` verteilt die Kommandos als Vergleichskette auf dem Klartextbyte `[0]`.
Aufrufe laufen über `bal`, die Funktionsnamen stehen im vorangehenden
`lw $t9, …($gp)` — daher die saubere Zuordnung:

| Cmd | Handler ruft | Bedeutung |
|-----|--------------|-----------|
| 10  | `safe_write`, `playBootAnnimation` | Sync, liefert Gerätekennung |
| 11  | **`system("reboot")`** | startet das Panel-Linux neu — rund 5 s |
| 12  | **`system("halt")`** | fährt das Panel-Linux herunter — nicht senden |
| 13  | `SaveConfig` | Drehung sichern |
| 14  | `set_brightness` | Helligkeit |
| 15  | `set_framerate` | Bildrate |
| 38  | `saveFd`, `saveFp` | Datei zum Schreiben öffnen |
| 39  | `uart_read`, `save_file` | Block anhängen |
| 40  | `uart_read`, `save_small_file` | kleine Datei am Stück |
| 42  | **`DeleteFile`** | Datei löschen |
| 98  | `GetFileSize` | Dateigröße |
| 99  | `dir_result`, `safe_write` | Verzeichnisliste abrufen |
| 100 | `QuerryStorage`, `GetDir` | Speicherinfo |
| 101 | `show_jpg_buffer` | JPEG-Vollbild |
| 102 | `decode_png` | PNG-Vollbild |
| 110 | `play_local_h264_async` | lokales Video starten |
| 111 | `stop_local_h264` | lokales Video stoppen |
| 112 | `local_h264_playing` | läuft lokales Video? |
| 113 | `show_jpg_local` | lokales JPEG zeigen |
| 114 | `ImgPlaying` | Bildwiedergabe abfragen |
| 121 | `push_h264_block` | H.264-Block |
| 122 | `h264_block_cnt` | Warteschlangentiefe |
| 123 | `stop_h264` | Strom beenden |
| 125 | `SaveConfig` | Einstellungen sichern |
| 201 | `group`, `position` | Gerätegruppe/Position |
| 240 | `chip_id_buff` | Chipkennung |
| 253 / 254 | `test_h264` | Testmodus |

### Cmd 15 bestimmt den Abspieltakt — und damit die Verzögerung

`set_framerate(fps)` setzt `frameTime = 1000 / fps` Millisekunden. Der
Abspielthread `play_h264_thread` decodiert je Durchlauf ein Bild
(`v4l2_h264_decoder_direct_work`, `v4l2_h264_decoder_get_frame_info`), misst
per `gettimeofday` gegen `lastframe_time` und schläft mit `usleep` den Rest
von `frameTime`. Bei leerer Warteschlange wartet er über `pthread_cond_wait`,
bis `push_h264_block` signalisiert.

Die Ausgabe hält also streng den gemeldeten Takt. Wer genau mit dieser Rate
liefert, behält jeden einmal entstandenen Rückstand dauerhaft — der Player holt
nie auf. Meldet man eine höhere Rate als man liefert (am 13.09.2026: 30
gemeldet, 25 geliefert), läuft er leer und zeigt jedes Bild, sobald es da ist.
Siehe `wiedergabe.geraet_fps` in `einstellungen.yaml`.

### Achtung: Cmd 12 fährt das Gerät herunter

Der Handler ruft `system("halt")` mit fest eingebauter Zeichenkette. Die
ältere Tabelle führte 12 als „Drehung setzen" — das ist Cmd 13
(`set_rotation` plus `SaveConfig`). Danach hilft nur noch Strom ab und dran.

### Cmd 11 ist der Rettungsanker — und liegt direkt daneben

In dieser Tabelle stand bei 11 lange ein Strich, weil der Zweig kein `bal` auf
eine benannte Funktion enthält. Er ruft `system()` über den GOT direkt auf; das
Argument steht im Delay-Slot des `jalr`:

```
Cmd 11 -> 0x402fd0   lw    $t9, -0x7c74($gp)     ; system
                     jalr  $t9
                     addiu $a0, $a0, -0x25e8     -> 0x0040da18 = "reboot"

Cmd 12 -> 0x402fe4   lw    $t9, -0x7c74($gp)     ; system
                     jalr  $t9
                     addiu $a0, $a0, -0x25e0     -> 0x0040da20 = "halt"
```

`$a0` ist in beiden Fällen 0x00410000, geladen im Delay-Slot des `beq` der
Vergleichskette. **Acht Byte Zeichenkettenadresse trennen „kommt in fünf
Sekunden wieder" von „bleibt aus, bis der Strom weg war."** Deshalb steht 11 in
`tools/panel_neustart.py` als benannte Konstante, mit 12 als Warnschild
daneben.

Damit ist der hängende Dekoder (siehe `WIEDERHERSTELLUNG.md`) ohne Kabelziehen
zu beheben. Der Neustart ist sogar der sanftere Weg: `reboot` läuft über init,
mit Sync und ausgehängten Dateisystemen — ein Stromabriss nicht, und in
`app.cfg` steht der Pfad des Standby-Clips.

Am Gerät gemessen (09.09.2026): vom Bus verschwunden nach 2,9 s, wieder da nach
1,8 s, antwortet danach auf Sync.

### Achtung: Cmd 42 ist ein Löschbefehl

Unsere Video-Initialisierung schickt 42 mit, weil TURZX es auch tut. Der Handler
liest die Namenslänge aus `[8..11]` und den Namen ab `[16]`; unser Paket lässt
beides auf null, `DeleteFile("")` scheitert an `lxstat` und tut nichts. Der
Startpfad ist damit unbedenklich — aber das Feld ist scharf, sobald es gefüllt
wird.

### Nutzlast der Dateikommandos

Einheitlich im entschlüsselten 500-Byte-Klartext:

```
[8..11]   BE  Länge des Namens
[12..15]  BE  Länge der Datei      (nur Cmd 40)
[16..]        Name, roh
danach        Dateibytes über dieselbe Bulk-Pipe (nur Cmd 40)
```

`save_small_file` macht `fopen(name, "w+")` mit dem Namen **unverändert** — kein
Basisverzeichnis, keine Beschränkung. `max_packet_len` ist 1 MiB, so groß darf
die Datei sein.

**Pfade müssen relativ sein.** Am Gerät am 2026-09-03 gemessen: `/usr/data`,
`/media`, sogar `/` liefern alle `nodir`, während `usr/data`, `media` und `.`
sauber auflisten. Das Arbeitsverzeichnis der Anwendung ist die Wurzel, führende
Schrägstriche werden abgewiesen. Die 8.8"-Binärdatei in `fw/` reicht den Namen
noch unverändert an `opendir` weiter — die Firmware auf dem 9.2er tut das
offenbar nicht mehr. Für dieses Gerät gibt es keine Datei in `fw/`; die dortigen
beiden sind byte-identisch und decken 8.8" und 8" ab.

Dateigrößen aus Cmd 98 kommen **little-endian** in `[8..11]` zurück — anders als
die Längenfelder im Kommando, die big-endian sind.

`GetDir` ist **nicht** rein lesend: fehlt das Verzeichnis, legt es die Firmware
an (`-createdone`) oder scheitert daran (`-createfailed`). Beim Stöbern also
bedenken, dass ein Tippfehler ein leeres Verzeichnis hinterlässt.

Cmd 99 ist zustandsbehaftet: der Handler merkt sich einen Versatz in
`dir_result` (0x2800 groß) und schiebt ihn je Aufruf um `cmd_len` (512) weiter,
bis er umläuft. Nur **bei Versatz 0** wird der mitgeschickte Pfad überhaupt
gelesen und `GetDir` ausgeführt. Deshalb immer alle 20 Stücke abholen — sonst
steht der Zeiger mitten im Puffer und die nächste Abfrage listet nicht neu.

### Startlogo

```c
showLogo() {
    if (access("/usr/data/boot.jpg", F_OK) == -1)
            show_jpg_local("/media/logo.jpg");     // Werkslogo
    else    show_jpg_local("/usr/data/boot.jpg");  // eigenes
}
```

Das Werkslogo liegt auf einer anderen Partition und wird nie beschrieben. Ein
eigenes Logo ist eine reine Ergänzung: fehlt oder taugt es nichts, erscheint
wieder das Original. `tools/panel_files.py bootlogo --entfernen` stellt den
Auslieferungszustand her.

`/usr/data/boot.sign` gehört **nicht** dazu. `main` öffnet die Datei beim Start
mit `"w+"` und schließt sie sofort wieder — eine Markierung, keine Signatur. Es
gibt im Logo-Pfad keine Integritätsprüfung.

### Warum das Panel beim Hochfahren noch etwas anzeigt

Es hat keine Erkennung für „kein Signal". Der Bildspeicher bleibt erhalten,
solange Strom anliegt, und die meisten Boards legen im ausgeschalteten Zustand
+5 V Standby auf USB. Sichtbar ist also schlicht das zuletzt gesendete Bild —
weshalb der Dienst beim Beenden ein gewolltes hinterlässt (`tools/splash.py`).
War das Panel dagegen länger stromlos, ist der Speicher leer und `showLogo`
greift.


## Lokale Wiedergabe: 11 Bilder je Block

Der Voreinstellungsmodus (`cfg[1]`) spielt beim Wiederauftauchen des Hosts eine
lokale Datei — `play_local_jpg_async` für Bilder, `play_local_h264_async` für
Video. Für ein Standbild ist **Video die richtige Wahl**, weil nur dieser Pfad
den Puffer vorher leert:

```c
play_local_h264_async(name) {
    clean_fb1();
    strcpy(local_264_path, name);
    strcpy(cfg + 6, name);      // Name landet in der Konfiguration
    pthread_create(..., play_local_h264_thread, ...);
}
```

Der JPEG-Pfad räumt nicht auf. Was das Bild nicht abdeckt, bleibt
uninitialisierter Speicher — sichtbar als Rauschen. Verschärft wird das
dadurch, dass `show_jpg_buffer` mit `dev_width` rechnet, während der
H.264-Pfad bei `dev_type == 7` ausdrücklich auf 480 × 1920 überschreibt:

```
0x00407834  bne  $a2, $a1, ...     ; dev_type != 7 ?
0x0040783c  addiu $v0, $zero, 0x1e0 ; sonst  480
0x00407844  addiu $v0, $zero, 0x780 ;        1920
```

Der physische Bildspeicher dieses Geräts ist laut `open_fb` **464 × 1920**
(dev_type 7, ohne Korrektur). Der JPEG-Pfad zeichnet also 464 breit, der
Videopfad 480.

### Die Bildgrenze

Aus einem eingereichten Block verarbeitet der Dekoder nur **11 Bilder**.
Danach hält er das letzte, die Firmware liest die Datei von vorn (`fseek(0)`)
und schiebt sie erneut ein, gebremst durch
`while (h264_block_cnt >= 3) sleep(1)` — siehe Nachtrag unten: die Grenze gilt
je Sequenzstart, und der Nachschub kommt nur einmal je Sekunde.

Gemessen am 2026-09-03 mit einem Zählclip aus 60 groß bezifferten Bildern: die
Anzeige blieb bei **11** stehen. Bei 129 KB und bei 196 KB gleichermaßen — es
ist eine Bild-, keine Bytegrenze. Die Warteschlange selbst ist ein Ring aus
fünf Blöcken zu je 202 756 B, der NAL-Puffer ist `max(dev_width·dev_height,
128 KiB)`, also rund 890 KB; beides scheidet als Ursache aus. Vermutlich ist es
die Anzahl der Ausgabepuffer des V4L2-Dekoders.

**Folge für die Gestaltung:** eine Bewegung über die Fläche braucht viele
Bilder und wirkt in elf wie ein Stroboskop. Eine Helligkeitspulsation wirkt
auch in elf Schritten weich. Deshalb atmet die Wortmarke im Standby, statt dass
ein Lichtstrich über sie wandert.

### Nachtrag 2026-09-16: Grenze je Sequenz, Takt, Nachschub

Am Gerät gemessen, ohne Kamera: Cmd 122 liefert `h264_block_cnt`, und der
Player nimmt einen Block heraus, **bevor** er dessen Bilder zeigt. Der Abstand
zweier Abnahmen ist also die wahre Dauer eines Durchlaufs
(`scratchpad/lokal_takt_messen.py`, Abfrage alle 5 ms; bei 25 ms dieselben
Befunde).

**Die 11 gelten je Sequenzstart, nicht je Datei.** 33 Bilder mit
Schlüsselbild alle 11 und SPS/PPS davor laufen vollständig: 1,41 s bei 25 fps
(Soll 1,32). Mit nur einem Schlüsselbild dauern dieselben 33 Bilder 1,5–2,4 s
(11 gezeigt, der Rest wartet ab), ohne wiederholte SPS/PPS 4,2–4,4 s. Jeder
Sequenzstart kostet im Mittel rund 30 ms, mal nichts, mal ~90 ms am Stück.

**Leser und Player** (Disassemblat `play_local_h264`, `play_h264_thread`):

```c
play_local_h264(path) {                // Lesefaden
    buf = malloc(0x31800);             // 202 752 B je Block
    for (;;) {
        n = fread(buf, 1, 0x31800, f);
        if (n <= 0) { if (loop) { fseek(f, 0, 0); continue; } break; }
        push_h264_block(buf, n);
        while (h264_block_cnt >= 3) sleep(1);   // Nachschub nur je Sekunde
    }
}
play_h264_thread() {                   // Player
    for (;;) {
        lock; while (h264_block_cnt == 0) cond_wait; ...
        if (sekunden() - letzte_freigabe >= 11)  // alle >= 11 s:
            v4l2_h264_decoder_work_release();    // Dekoder freigeben
        nalu_buf_write(block); h264_block_cnt--; unlock;
        je NAL: Slice -> dekodieren, zeigen, usleep(Rest von frameTime)
    }
}
```

Der Leser füllt die Schlange einmal je Sekunde auf drei Blöcke auf. Reichen
drei Durchläufe nicht über diese Sekunde, steht das Bild bis zum nächsten
Nachfüllen.

**Der Takt der lokalen Wiedergabe ist der zuletzt per Cmd 15 gesetzte.**
`frameTime` liegt in `.sbss`; `init_disp` setzt 30 fps, sonst nur der
Cmd-15-Handler. Das Panel hängt an +5 V Standby und startet beim Hochfahren des
PCs nicht neu. Nach unserem Dienst (Cmd 15 = 60) spielte der Ladebildschirm
deshalb mit 60 fps: elf Bilder in 190 ms, die Schlange bei 51 % der Abfragen
leer, gut die Hälfte der Zeit Stillstand. Der Nutzer sah „nicht flüssig".

Abhilfe, beide gemessen: Der Dienst setzt beim Beenden 30 fps
(`splash.LOKAL_TAKT`). Danach dauert ein Durchlauf 1,1–1,3 s, und die Schlange
ist nie leer. Der Clip hat 33 Bilder in drei Sequenzen. Damit bleibt die
Schlange auch bei 60 fps (Dienst abgestürzt) nie leer: 0,59–0,88 s je
Durchlauf, drei Blöcke reichen über die Sekunde.

**Wer die lokale Wiedergabe startet:** `usb_state_thread` fragt einmal je
Sekunde `get_usb_state` ab. Beim Wechsel auf „verbunden" startet er je nach
`cfg[1]` JPEG oder H.264. Ein Abfall für weniger als 10 s zählt nicht als
Wechsel. Erst nach 10 s dunkelt er ab (`cfg[5]`) oder stoppt.


## Cmd 110 schreibt in die Konfiguration — mit Folgen

`play_local_h264_async(name)` macht drei Dinge, nicht eines:

```c
clean_fb1();                 // Bildspeicher leeren
strcpy(local_264_path, name);
strcpy(cfg + 6, name);       // Name landet DAUERHAFT in der Konfiguration
```

Unsere Video-Initialisierung schickte Cmd 110 lange mit **leerem** Namen, weil
nur die Wirkung von `clean_fb1()` gebraucht wurde. Direkt danach folgt Cmd 13,
dessen Handler `SaveConfig()` ruft — der leere Pfad wanderte damit in
`app.cfg`. Beim nächsten Start fand `playPresetMode` unter `cfg[1] = 2` einen
leeren Dateinamen und spielte nichts. Am 2026-09-03 genau so beobachtet: das
Standbild verschwand nach einigen Neustarts.

Die Init schickt deshalb jetzt den Pfad des Standby-Clips mit. Cmd 111 direkt
danach stoppt die kurz angestoßene Wiedergabe wieder.

### Dauerhaftigkeit

Was einen Stromausfall überlebt:

| Was | Wo | Gesetzt durch |
|-----|-----|---------------|
| Der Clip selbst | `/usr/data/standby.h264` im Flash | Cmd 40 |
| Startmodus, Helligkeit, Rotation | `/usr/data/app.cfg` | Cmd 125 → `SaveConfig` |
| Pfad des lokalen Videos (`cfg+6`) | dieselbe Datei | Cmd 110, gesichert von Cmd 13 oder 125 |

`ReadConfig` lädt das beim Einschalten zurück. Damit eine einmal verstellte
Konfiguration nicht dauerhaft falsch bleibt, **bestätigt die Init die Werte bei
jedem Start** mit einem eigenen Cmd 125. Das kostet einen Befehl und macht die
Einstellung gegen alles immun, was sie zwischendurch verändert.
