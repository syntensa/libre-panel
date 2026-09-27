# Video über USB — gelöst und am Gerät verifiziert (2026-09-02)

**Live-H.264 mit 25 fps läuft auf dem Panel.** Damit ist der Transport fertig.

## Das Panel ist zweischichtig

Das ist der Schlüssel zum Verständnis, und es erklärt nebenbei die „1 FPS",
mit denen dieses Projekt angefangen hat.

| Schicht | Kommando | Inhalt |
|---|---|---|
| Hintergrund | **121** | H.264-Strom, Annex-B, Constrained Baseline, 480×1920 |
| Overlay | **102** | RGBA-PNG mit Transparenz, wird über das Video komponiert |

Die Originalsoftware zeichnet ihr Sensor-Overlay etwa **einmal pro Sekunde**
neu — daher der Eindruck, das Panel könne nur 1 fps. Das Video darunter läuft
die ganze Zeit flüssig. Die Grenze lag nie in der Hardware.

Aus dem Mitschnitt (`captures/turzx.pcapng`, TURZX spielt das AMD-Theme):

    0.0 ms   122  Streamstatus
  119.6 ms   102  PNG-Overlay        +34349 B     464×1920 RGBA, 92.6 % transparent
 1223.7 ms   102  PNG-Overlay        +37763 B
 2075.1 ms   121  H.264-Chunk        +202752 B
 2422.1 ms   102  PNG-Overlay        +37208 B
 ...
           121  Groesse 160803  [12]=1   <== Clip-Ende, dann von vorn

Die Chunk-Summe eines Durchlaufs ergibt **exakt 2 593 827 Byte** — die Groesse
von `AMD.mp4.h264` auf der Platte. TURZX schickt also den ganzen Clip, markiert
das Ende mit `[12]=1` und beginnt erneut.

## Die Init-Sequenz

    10   Sync
    110  Videomodus            <-- ohne dieses Kommando bleibt der Schirm schwarz
    111  Video-Init
    112  Video-Init
    13   Video-Init
    14   Helligkeit (0..102)
    42   Video-Init            <-- TURZX nutzt 42; die Referenzlib schickt 41
    102  Overlay leeren        <-- RGBA, alpha 0, VOLLSTAENDIG DURCHSICHTIG
    15   Framerate
    17   Chunkgroesse abfragen (Geraet meldet 0 -> Default 202752 gilt)

**110 kannte die Referenzbibliothek nicht.** Ermittelt durch einen Sweep über
acht Init-Sequenzen: nur die beiden mit 110 zeigten überhaupt ein Bild.

## Die zwei Fehler, die einen Abend gekostet haben

### 1. Deckendes statt durchsichtiges Overlay
Unser „Schwarzbild" in der Init war `(0, 0, 0, 255)` — **alpha 255, also
blickdicht**. Es legte sich über das Video: man sah es einen Sekundenbruchteil
aufblitzen und danach nichts mehr. Richtig ist `(0, 0, 0, 0)`.

Das eingebaute Clear-Bild der Referenzbibliothek besteht aus lauter Nullen —
bei RGBA heisst das alpha 0. Der Unterschied fällt beim Lesen nicht auf.

### 2. Vierzigfaches Tempo
`play` schickte 6.7 MB/s. TURZX schickt zwei 202752er Chunks alle rund 2.4 s,
also etwa **170 KB/s** — ungefähr die Bitrate des Clips. `--rate` bremst jetzt
darauf; im Live-Betrieb ergibt sich das Tempo von selbst aus der Bildrate.

## Was zusätzlich ausgeschlossen wurde
Multi-Slice-NALs (`sliced-threads=0` ist trotzdem noetig, siehe unten),
Chunkgroesse, Bildrate 24 gegen 25, die Statusabfrage per Cmd 122, das
Endflag `[12]=1`, und unser Encoder — die Referenzdatei verhielt sich identisch.

## Encoder-Einstellungen
`-tune zerolatency` schaltet **`sliced-threads=1`** ein und zerlegt jedes Bild
in eine Scheibe pro Thread (hier 16 NAL-Einheiten pro Frame). Die Referenzvideos
des Geraets haben genau **eine** Slice-NAL pro Frame. `sliced-threads=0` ist
gesetzt; `threads=1` vermeidet zusaetzlich die Frame-Threading-Latenz und
schafft trotzdem 104 fps.

Die SPS-Bytes unseres Stroms sind identisch zu denen der Referenzdateien:
`67 42 c0 1f da 07 80 f1` — profile_idc 66, constraint 0xc0, Level 3.1.

## Geometrie
Der Framebuffer ist **480×1920 hochkant**, gedreht mit `ROTATE_270` aus der
1920×480-Designflaeche. TURZX' Overlays im Mitschnitt sind **464×1920** — ein
Erbstueck der 8.8"-Variante, die dieselben Themes nutzt und verbreiteter ist.
Unser 480er Testbild zeigte alle vier Randbaender, 480 ist fuer dieses Geraet
also richtig.

## Was das fuer SPUR II bedeutet
Zwei Wege stehen offen:

1. **Alles ins Video.** 25 fps fuer das gesamte Bild, Text wird vom Encoder
   leicht weichgezeichnet — bei 480 px Breite spuerbar.
2. **Zweischichtig wie TURZX.** Bewegtes (Verlaeufe, Glow, scrollende Graphen)
   als H.264-Hintergrund, scharfe Zahlen als RGBA-Overlay darueber. Text bleibt
   pixelscharf. Offen: wie oft das Overlay wirklich gehen darf — TURZX nutzt
   1 Hz, aber das war schon bei den Standbildern nur dessen Wahl.

Zu messen: die maximale Overlay-Rate. Der Bildpfad allein schafft 9 fps
(87 ms Geraetezeit pro Vollbild); ob ein transparentes Overlay ueber laufendem
Video schneller geht, ist offen.
