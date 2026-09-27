#!/usr/bin/env python3
"""
panel_video.py — H.264 über USB, der Weg für bewegte Bilder.

Warum nicht der Bitmap-Pfad: gemessen bremst das Panel den Bulk-OUT, bis es den
vorigen Vollframe verarbeitet hat — rund 60 ms Grundlast plus etwa 0.25 ms pro
KB. Das deckelt bei 11–16 fps, unabhängig davon, wie schnell wir rendern.
Der Onboard-H.264-Decoder umgeht genau diesen Flaschenhals; deshalb gibt es ihn.

Zwei Betriebsarten:

  play <datei.h264>   fertigen Annex-B-Strom abspielen. Erster Beweis: trennt
                      den Transport von unserem Encoder. Referenzdateien liegen
                      in ../video/4801920/.
  live                Frames rendern -> ffmpeg -> Panel, in Echtzeit.

Der Bildstrom selbst ist flüchtig: es wird nichts im Gerät gespeichert. Cmd 125
schickt die Init inzwischen doch — aber nur für die Standby-Einstellung, siehe
`start()`. Cmd 11 (Neustart) steht in `panel_neustart.py`, Cmd 12 nirgends.

VOR DEM AUFRUF: TURZX über das Tray beenden.
"""
from __future__ import annotations

import argparse
import os
import queue
import subprocess
import sys
import threading
import time
from io import BytesIO

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from panel_probe import (  # noqa: E402
    CMD_BRIGHTNESS, CMD_FRAMERATE, CMD_H264_CHUNK_SIZE, CMD_STREAM_STATUS,
    CMD_STREAM_STOP, CMD_SYNC, CMD_UPLOAD_PNG, DEFAULT_ROT, DESIGN_H, DESIGN_W,
    WIRE_H, WIRE_W, Panel, build_packet, put_size_be, resp_ok, seal, to_wire,
)

CMD_PLAY_H264 = 121
CMD_LOKALES_VIDEO = 110
CMD_CFG = 125

# Startmodus 2 = beim Verschwinden und Wiederauftauchen des Hosts das lokale
# Video abspielen (1 waere ein lokales JPEG, 0 nichts).
STARTMODUS_VIDEO = 2

# Der Clip, den das Panel im Standby von sich aus abspielt. Der Name steht
# hier, weil die Init-Sequenz ihn braucht — siehe start().
LOKALES_STANDBY = "usr/data/standby.h264"
# Startet man unter Windows ein Konsolenprogramm aus einem Prozess OHNE eigene
# Konsole — etwa den Dienst unter `pythonw.exe` im Autostart —, bekommt es ein
# eigenes schwarzes Fenster. CREATE_NO_WINDOW unterdrueckt das. Auf anderen
# Systemen gibt es das Flag nicht, deshalb der Standardwert 0.
OHNE_FENSTER = getattr(subprocess, "CREATE_NO_WINDOW", 0)
DEFAULT_CHUNK = 202752          # Gerät meldet auf Cmd 17 eine 0 -> Default gilt
# ffmpeg liegt seit dem Umzug nach D: IM Projekt (bin/ffmpeg.exe) statt im
# TURZX-Installationsordner darueber. Der alte Pfad wird noch geprueft, damit
# eine Kopie am alten Ort weiterlaeuft.
PROJEKT_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
INSTALL_DIR = os.path.abspath(os.path.join(PROJEKT_DIR, ".."))
FFMPEG = os.path.join(PROJEKT_DIR, "bin", "ffmpeg.exe")
if not os.path.exists(FFMPEG):
    FFMPEG = os.path.join(INSTALL_DIR, "ffmpeg.exe")


# --------------------------------------------------------------------------
# Video-Sitzung
# --------------------------------------------------------------------------

def clear_png() -> bytes:
    """Vollstaendig DURCHSICHTIGES RGBA-Bild.

    Das Panel arbeitet zweischichtig: Cmd 121 liefert das H.264-Hintergrund-
    video, Cmd 102 legt ein RGBA-Overlay darueber. Am Mitschnitt von TURZX
    abgelesen — dessen Overlays sind 464x1920 RGBA und zu 92.6 % transparent.

    Hier stand vorher `(0, 0, 0, 255)`, also DECKENDES Schwarz. Das legt sich
    blickdicht ueber das Video: man sieht es genau einen Moment lang aufblitzen
    und danach nichts mehr. Genau dieses Verhalten hat uns einen Abend gekostet.
    Das eingebaute Clear-Bild der Referenzbibliothek besteht aus lauter Nullen —
    bei RGBA heisst das alpha 0, durchsichtig.
    """
    from PIL import Image
    buf = BytesIO()
    Image.new("RGBA", (WIRE_W, WIRE_H), (0, 0, 0, 0)).save(
        buf, format="PNG", compress_level=1)
    return buf.getvalue()


class VideoSession:
    """Init-Sequenz, Chunk-Versand und Flusskontrolle.

    Reihenfolge der Init übernommen aus send_video der Referenzbibliothek:
    111 -> 112 -> 13 -> 14 -> 41 -> 102(Clear) -> 15(fps) -> 17(Chunkgröße).
    """

    def __init__(self, panel: Panel, fps: int = 25, brightness: int = 32,
                 verbose: bool = True):
        self.p = panel
        self.fps = fps
        self.verbose = verbose
        self.chunk_size = DEFAULT_CHUNK
        self.sent_bytes = 0
        self.sent_chunks = 0
        self.waits = 0
        self.depth_max = 0
        # Wann der erste Block ging, und die ersten Bremsungen mit Zeitpunkt —
        # um zu sehen, OB sie nur beim Anlaufen kommen oder auch im Betrieb.
        self.t_start: float | None = None
        self.bremsprotokoll: list = []
        self.hoch = 2          # ab diesem Fuellstand bremsen
        self.tief = 1          # bis auf diesen abwarten
        self.poll = True       # Cmd 122 ueberhaupt abfragen?
        # Nur jeder n-te Chunk fragt den Status ab. 4 statt 12, und der Grund
        # steht in der Firmware: die Warteschlange des Panels ist ein Ring aus
        # GENAU FUENF Bloecken, und `push_h264_block` ueberschreibt beim
        # Ueberlauf einen ungelesenen — still, ohne Fehler. Bei 12 lag zwischen
        # zwei Abfragen rund eine halbe Sekunde; darin kann der Ring von 2 auf
        # 5 laufen, und der ueberschriebene Block ist genau das, was man als
        # zerrissenes Bild sieht. Die Abfrage selbst kostet eine kurze
        # USB-Transaktion.
        self.poll_every = 2
        self._brightness = brightness

    def start(self) -> None:
        """Init-Sequenz.

        **Kommando 110 ist der Schluessel.** Ohne es bleibt das Panel schwarz,
        egal was sonst geschickt wird — am 2026-09-02 durch einen Sweep von acht
        Sequenzen ermittelt: nur die beiden mit 110 zeigten ueberhaupt ein Bild.
        Die Referenzbibliothek kennt 110 nicht; sie schickt stattdessen 41,
        das TURZX gar nicht verwendet (TURZX hat 42).

        Warum es wirkt, war lange unklar und steht inzwischen im Code der
        Firmware: `play_local_h264_async` beginnt mit `clean_fb1()`. Es startet
        also gar kein Video — es LEERT den Bildspeicher. Das direkt folgende
        Kommando 111 haelt die angestossene Wiedergabe sofort wieder an.

        **Der Dateiname ist Pflicht, nicht Zierde.** Derselbe Handler macht
        `strcpy(cfg + 6, name)`, und das anschliessende Kommando 13 ruft
        `SaveConfig()`. Mit leerem Namen loescht die Init also bei jedem Start
        den Pfad des Standby-Clips — erst im Speicher, dann dauerhaft in
        `app.cfg`. Am 2026-09-03 genau so passiert: das Standbild erschien nach
        einigen Neustarts nicht mehr. Deshalb schicken wir hier den richtigen
        Pfad mit.
        """
        p = self.p
        p.cmd(CMD_SYNC, label="Sync")
        # 50 statt 200 ms (2026-09-15): die Quittung auf Sync kommt synchron
        # zurueck (gemessen 0 ms), und Panel() hat die Pipe davor schon mit
        # `ensure_sync` geprueft. Die 0,2 s stammten aus der Referenzbibliothek.
        time.sleep(0.05)
        roh = LOKALES_STANDBY.encode("utf-8")
        pkt = build_packet(CMD_LOKALES_VIDEO)
        put_size_be(pkt, len(roh))
        pkt[16:16 + len(roh)] = roh
        p.cmd(CMD_LOKALES_VIDEO, packet=pkt, label="Bildspeicher leeren")
        for cid in (111, 112, 13):
            p.cmd(cid, label="Video-Init")
        pkt = build_packet(CMD_BRIGHTNESS)
        pkt[8] = self._brightness
        p.cmd(CMD_BRIGHTNESS, packet=pkt, label=f"Helligkeit {self._brightness}")
        p.cmd(42, label="Video-Init")

        png = clear_png()
        pkt = build_packet(CMD_UPLOAD_PNG)
        put_size_be(pkt, len(png))
        p.cmd(CMD_UPLOAD_PNG, packet=pkt, data=png, label="Overlay leeren (transparent)")

        pkt = build_packet(CMD_FRAMERATE)
        pkt[8] = self.fps
        p.cmd(CMD_FRAMERATE, packet=pkt, label=f"{self.fps} fps")

        r = p.cmd(CMD_H264_CHUNK_SIZE, label="Chunkgroesse")
        if r and len(r) >= 12:
            negotiated = int.from_bytes(r[8:12], "big")
            if 0 < negotiated <= 1024 * 1024:
                self.chunk_size = negotiated
                if self.verbose:
                    print(f"      -> Geraet meldet {negotiated}")
            elif self.verbose:
                print(f"      -> Geraet meldet 0, bleibe bei {self.chunk_size}")

        # Die Standby-Einstellung bei JEDEM Start bestaetigen, statt darauf zu
        # vertrauen, dass sie niemand anfasst. Cmd 125 schreibt cfg[0..5] und
        # ruft SaveConfig; der Pfad in cfg+6 stammt aus dem Cmd 110 oben und
        # wird dabei mitgesichert. Kostet einen Befehl beim Start und macht die
        # Einstellung gegen alles immun, was sie zwischendurch verstellt —
        # genau das war am 2026-09-03 passiert, als unsere eigene Init sie mit
        # einem leeren Pfad ueberschrieb.
        pkt = build_packet(CMD_CFG)
        pkt[8] = self._brightness          # Helligkeit im Standby
        pkt[9] = STARTMODUS_VIDEO          # lokales Video
        # [10] res, [11] Rotation, [12] Sleep, [13] Offline bleiben 0:
        # Rotation 0 setzen wir ohnehin ueber Cmd 13, res und Sleep werden in
        # der Firmware nirgends ausgewertet, und Offline 0 heisst abdunkeln,
        # wenn der Host verschwindet.
        p.cmd(CMD_CFG, packet=pkt, label="Standby-Einstellung sichern")

    def queue_depth(self):
        """Fuellstand der Dekoder-Warteschlange (Cmd 122, resp[8]).

        Kurzer Timeout und stiller Fehler: waehrend das Geraet dekodiert,
        antwortet es auf Statusabfragen manchmal nicht rechtzeitig. Das ist
        kein Problem des Bildstroms — es lohnt nicht, deswegen die Konsole
        vollzuschreiben. `None` heisst schlicht "gerade nicht bekannt".
        """
        st = self.p.xfer(seal(build_packet(CMD_STREAM_STATUS)),
                         timeout=400, still=True)
        if not st or len(st) <= 8:
            return None
        d = st[8]
        self.depth_max = max(self.depth_max, d)
        return d

    def drain(self, low: int = 1, timeout: float = 1.5) -> None:
        """Warten, bis die Warteschlange wirklich abgeflossen ist.

        Das ist die eigentliche Flusskontrolle. Vorher wurde bei voller Schlange
        nur 20 ms gewartet und dann weitergeschickt — dabei ging in 10 s die
        67-fache Datenmenge raus (das 40-Fache der Echtzeit), der Dekoder
        erstickte und zeigte das Bild nur einen Sekundenbruchteil.
        """
        t0 = time.perf_counter()
        gewartet = False
        erste = None
        while time.perf_counter() - t0 < timeout:
            d = self.queue_depth()
            if erste is None:
                erste = d
            if d is None or d <= low:
                if gewartet:
                    self._bremsung(t0, erste, d)
                return
            gewartet = True
            time.sleep(0.030)      # nicht haemmern, das Geraet ist beschaeftigt
        self._bremsung(t0, erste, None)

    def _bremsung(self, t0, von, bis) -> None:
        """Zaehlt eine Bremsung; die ersten zwanzig kommen mit Zeitpunkt ins Log.

        Seit das Panel eine hoehere Bildrate gemeldet bekommt als geliefert wird
        (`wiedergabe.geraet_fps`), bremst die Flusskontrolle im Betrieb nicht
        mehr — nur noch beim Anlaufen. Das Protokoll zeigt, wann genau.
        """
        self.waits += 1
        if len(self.bremsprotokoll) < 20:
            seit = t0 - (self.t_start if self.t_start is not None else t0)
            dauer = time.perf_counter() - t0
            self.bremsprotokoll.append((seit, self.sent_chunks, von, bis, dauer))
            print(f"[brems] +{seit:.2f} s nach dem ersten Block, Block "
                  f"{self.sent_chunks}: Schlange {von} -> {bis} in "
                  f"{1000 * dauer:.0f} ms", flush=True)

    def send_chunk(self, data: bytes, last: bool = False) -> bool:
        if self.t_start is None:
            self.t_start = time.perf_counter()
        pkt = build_packet(CMD_PLAY_H264)
        put_size_be(pkt, len(data))
        if last:
            pkt[12] = 1
        r = self.p.xfer(seal(pkt) + data)
        self.sent_bytes += len(data)
        self.sent_chunks += 1
        if r is None:
            return False
        if not self.poll or self.sent_chunks % self.poll_every:
            return True        # nicht nach jedem Chunk nachfragen
        d = self.queue_depth()
        if d is not None and d > self.hoch:
            self.drain(self.tief)
        return True

    def stop(self) -> None:
        self.p.cmd(CMD_STREAM_STOP, label="Stream stoppen")

    def report(self, elapsed: float) -> None:
        print(f"\n{self.sent_chunks} Chunks, {self.sent_bytes/1e6:.2f} MB in "
              f"{elapsed:.1f} s = {self.sent_bytes/elapsed/1e6:.2f} MB/s")
        print(f"Flusskontrolle hat {self.waits} mal gebremst, "
              f"hoechster Fuellstand {self.depth_max}")


# --------------------------------------------------------------------------
# play: fertigen Annex-B-Strom abspielen
# --------------------------------------------------------------------------

def do_play(path: str, loop: bool, brightness: int, fps: int,
            poll: bool = True, last_flag: bool = True,
            rate_kbs: float = 170.0) -> int:
    """Fertigen Annex-B-Strom abspielen.

    `rate_kbs` begrenzt das Tempo. Am Mitschnitt abgelesen schickt TURZX zwei
    202752er Chunks alle rund 2.4 s, also etwa 170 KB/s — ungefaehr die Bitrate
    des Clips. Ohne Bremse liefen hier 6.7 MB/s raus, das Vierzigfache; der
    Dekoder kam nicht mit.
    """
    if not os.path.exists(path):
        raise SystemExit(f"Datei nicht gefunden: {path}")
    size = os.path.getsize(path)
    print(f"Spiele {path} ({size/1e6:.2f} MB)")

    p = Panel(verbose=True)
    s = VideoSession(p, fps=fps, brightness=brightness)
    s.start()
    print("\nStrom läuft — Strg+C beendet.\n")
    p.verbose = False
    t0 = time.perf_counter()
    gesendet = 0
    try:
        while True:
            with open(path, "rb") as f:
                while True:
                    data = f.read(s.chunk_size)
                    if not data:
                        break
                    if rate_kbs > 0:
                        soll = t0 + gesendet / (rate_kbs * 1024.0)
                        jetzt = time.perf_counter()
                        if jetzt < soll:
                            time.sleep(soll - jetzt)
                    gesendet += len(data)
                    if not s.send_chunk(data, last=(last_flag and f.tell() >= size)):
                        print("[err ] Chunk abgelehnt")
                        return 1
            if not loop:
                break
    except KeyboardInterrupt:
        print("\nAbgebrochen.")
    finally:
        elapsed = time.perf_counter() - t0
        p.verbose = True
        s.stop()
        s.report(elapsed)
        p.close()
    return 0


# --------------------------------------------------------------------------
# live: rendern -> ffmpeg -> Panel
# --------------------------------------------------------------------------

# Abstand zweier Vollbilder in Sekunden. Siehe die Messung in ffmpeg_cmd:
# jedes Vollbild quantisiert den statischen Hintergrund neu und laesst die
# Kacheln sichtbar springen.
IDR_SEKUNDEN = 3

# ffmpeg schreibt seine Ausgabe ueber einen Puffer dieser Groesse (IO_BUFFER_SIZE
# des pipe-Protokolls): ein Bild kommt als n volle Stuecke plus ein kuerzeres
# letztes. Am 2026-09-14 an 249 Bildern nachgemessen: die Stuecke eines Bildes
# folgen im Abstand von 0,02 bis 0,25 ms, zwischen zwei Bildern liegen 40 ms,
# und kein Bild war ein genaues Vielfaches. Daran erkennt die Weiterleitung das
# Ende eines Bildes, ohne auf das naechste zu warten.
FFMPEG_PUFFER = 32768


def bild_zu_ende(laenge: int) -> bool:
    """Ist mit `laenge` gesammelten Bytes das Bild vollstaendig?

    Warum das zaehlt: das Panel misst seine Warteschlange in BLOECKEN. Ging ein
    Vollbild (bis 126 KB) als drei Bloecke raus, stand sie bei 3 und die
    Flusskontrolle bremste — gemessen genau alle drei Sekunden, im Takt der
    Vollbilder. Ein Bild je Block haelt sie bei 0 bis 1.

    Waere ein Bild doch einmal ein genaues Vielfaches, ginge es mit dem
    naechsten zusammen raus — ein Bild spaeter, nicht verloren.
    """
    return laenge > 0 and laenge % FFMPEG_PUFFER != 0


def ffmpeg_cmd(encoder: str, fps: int, crf: int, maxrate: str,
               threads: int = 1, drehen: int | None = None,
               preset: str = "ultrafast", vollbild_s: float | None = None) -> list[str]:
    """Constrained Baseline, wie die Referenzvideos des Geräts.

    **`drehen` (90/270): ffmpeg dreht, nicht Python.** Dann erwartet die
    Eingabe das Bild in Schreibtischlage (1920x480) und `-vf transpose` legt es
    hochkant. Am 2026-09-14 gemessen: Drehen und Kopieren in Python kostete den
    Renderfaden 2,0 ms je Bild, `tobytes` allein 0,6 ms — und der H.264-Strom ist
    BITGLEICH (gleicher MD5 ueber 60 Bilder, PSNR identisch). transpose=1 dreht
    im Uhrzeigersinn wie PILs ROTATE_270, transpose=2 gegen ihn wie ROTATE_90.
    Ohne `drehen` bleibt es beim alten Weg (Eingabe schon hochkant).

    **`vollbild_s` und `ipratio=2.0` (2026-09-14).** Das Pulsen beim Vollbild
    laesst sich nicht wegregeln — an 160 Studio-Bildern gemessen, ipratio
    1,4 / 2,0 / 2,8: jeweils ~56 % der ruhigen Flaeche > 2 Stufen, ohne AQ sogar
    68 % —, nur seltener machen: der Dienst nimmt `wiedergabe.vollbild_s` (10 s).
    ipratio 2,0 bleibt trotzdem: bei gleicher Datenrate +0,26 dB, Kanten +0,22 dB.

    Kein B-Frame, kein CABAC, Header vor jedem IDR — damit das Panel auch
    mitten im Strom aufsetzen kann.

    **Vollbild alle DREI Sekunden, nicht alle halbe.** Das war die Ursache
    der dauerhaften Artefakte im Kachelhintergrund.

    Der Hintergrund ist statisch: zwischen zwei Vollbildern ueberspringt der
    Encoder ihn vollstaendig (Skip-Bloecke), das Bild steht bombenfest. Bei
    JEDEM Vollbild wird er dagegen neu quantisiert — und faellt dabei minimal
    anders aus als vorher. Auf den hellen Elementen geht das unter, auf der
    fast flachen 8-px-Kachelung nicht: dort wandert eine ganze Flaeche um ein
    bis zwei Helligkeitsstufen. Bei einem Vollbild je halber Sekunde pulst
    also zweimal pro Sekunde der halbe Bildschirm.

    Gemessen an 60 echten Frames (zeitliche Streuung der im Original
    unbewegten Flaechen, nach Kodieren und Dekodieren):

        keyint  12 (0.5 s)   Flimmern 0.53   15.8 % der Flaeche > 2 Stufen
        keyint  25 (1 s)     Flimmern 0.52   14.0 %
        keyint  75 (3 s)     Flimmern 0.07    4.5 %      <- gewaehlt
        keyint 250 (10 s)    Flimmern 0.07    4.5 %

    Der Sprung liegt zwischen 25 und 75, darueber bringt es nichts mehr. Die
    Datenrate faellt nebenbei um ein Drittel (800 -> 541 kB/s), was der
    Warteschlange zum Panel zugutekommt.

    Gegengerechnet: Vollbilder sind auch die Stellen, an denen sich ein
    verlorener Block wieder heilt. Drei Sekunden ist der Preis dafuer — bei
    derzeit null verworfenen Chunks ein Fall, der nicht eintritt. Waere er
    haeufig, muesste man umgekehrt entscheiden. Encoder-Parameter helfen hier
    NICHT: Deblocking, AQ und crf 15 wurden gemessen und aenderten am
    Flimmern nichts (0.58 / 0.59 / 0.58).

    **`sliced-threads=0` ist der entscheidende Parameter.** `-tune zerolatency`
    schaltet Slice-Threading ein und zerlegt jedes Bild in eine Scheibe pro
    Thread — bei diesem Rechner 16 NAL-Einheiten pro Frame. Der Decoder des
    Panels verarbeitet das nicht: er quittiert jeden Chunk mit 0xC8 und zeigt
    trotzdem nichts. Die Referenzvideos des Geräts haben genau **eine**
    Slice-NAL pro Frame; danach richten wir uns.

    Statt Slice-Threading nutzen wir Frame-Threading mit wenigen Threads. Das
    kostet `threads - 1` Frames Latenz, deshalb die kleine Voreinstellung.
    """
    keyint = max(1, int(round(fps * (vollbild_s or IDR_SEKUNDEN))))
    groesse, filter_ = f"{WIRE_W}x{WIRE_H}", []
    if drehen in (90, 270):
        groesse = f"{DESIGN_W}x{DESIGN_H}"
        filter_ = ["-vf", "transpose=1" if drehen == 270 else "transpose=2"]
    base = [FFMPEG, "-hide_banner", "-loglevel", "error",
            "-f", "rawvideo", "-pix_fmt", "rgb24",
            "-s", groesse, "-framerate", str(fps), "-i", "-",
            "-an"] + filter_
    if encoder == "amf":
        return base + ["-c:v", "h264_amf", "-usage", "lowlatency",
                       "-profile:v", "constrained_baseline", "-quality", "speed",
                       "-rc", "vbr_latency", "-b:v", maxrate, "-maxrate", maxrate,
                       "-g", str(keyint), "-bf", "0",
                       "-pix_fmt", "yuv420p", "-f", "h264", "-"]
    return base + ["-c:v", "libx264", "-profile:v", "baseline",
                   "-preset", preset, "-tune", "zerolatency",
                   "-threads", str(threads),
                   "-crf", str(crf), "-maxrate", maxrate, "-bufsize", "2M",
                   "-x264-params",
                   f"sliced-threads=0:bframes=0:scenecut=0:"
                   f"keyint={keyint}:min-keyint={keyint}:"
                   f"ipratio=2.0:repeat-headers=1",
                   "-pix_fmt", "yuv420p", "-f", "h264", "-"]


def do_live(seconds: float, fps: int, encoder: str, crf: int, maxrate: str,
            brightness: int, rot: int, min_chunk: int, threads: int = 1,
            poll: bool = True) -> int:
    if not os.path.exists(FFMPEG):
        raise SystemExit(f"ffmpeg nicht gefunden: {FFMPEG}")
    import panel_bench as B

    cmd = ffmpeg_cmd(encoder, fps, crf, maxrate, threads)
    print("ffmpeg:", " ".join(cmd[1:12]), "...")
    proc = subprocess.Popen(cmd, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                            stderr=subprocess.PIPE, bufsize=0,
                            creationflags=OHNE_FENSTER)

    out_q: queue.Queue = queue.Queue()

    def reader():
        try:
            while True:
                b = proc.stdout.read(65536)
                if not b:
                    break
                out_q.put(b)
        finally:
            out_q.put(None)

    def errlog():
        for line in proc.stderr:
            txt = line.decode("utf-8", "replace").strip()
            if txt:
                print(f"[ffmpeg] {txt}")

    threading.Thread(target=reader, daemon=True).start()
    threading.Thread(target=errlog, daemon=True).start()

    p = Panel(verbose=True)
    s = VideoSession(p, fps=fps, brightness=brightness)
    s.start()
    print(f"\nLive-Strom, {seconds:.0f} s bei {fps} fps — Strg+C beendet.\n")
    p.verbose = False

    frames = 0
    pending = bytearray()
    t0 = time.perf_counter()
    t_render = 0.0
    try:
        while time.perf_counter() - t0 < seconds:
            target = t0 + frames / fps
            now = time.perf_counter()
            if now < target:
                time.sleep(target - now)

            r0 = time.perf_counter()
            im = B.render_bench_frame(frames / fps)
            wire = to_wire(im, rot).convert("RGB")
            t_render += time.perf_counter() - r0
            proc.stdin.write(wire.tobytes())
            frames += 1

            while True:
                try:
                    b = out_q.get_nowait()
                except queue.Empty:
                    break
                if b is None:
                    raise RuntimeError("ffmpeg hat sich beendet")
                pending += b
            if len(pending) >= min_chunk:
                s.send_chunk(bytes(pending))
                pending.clear()
    except KeyboardInterrupt:
        print("\nAbgebrochen.")
    except BrokenPipeError:
        print("\n[err ] ffmpeg-Pipe abgerissen")
    finally:
        elapsed = time.perf_counter() - t0
        try:
            proc.stdin.close()
        except Exception:
            pass
        time.sleep(0.3)
        while True:
            try:
                b = out_q.get_nowait()
            except queue.Empty:
                break
            if b is None:
                break
            pending += b
        if pending:
            s.send_chunk(bytes(pending), last=True)
        proc.terminate()
        p.verbose = True
        s.stop()
        print(f"\n{frames} Frames in {elapsed:.1f} s = {frames/elapsed:.1f} fps")
        print(f"Rendern+Drehen: {t_render/max(frames,1)*1000:.1f} ms pro Frame")
        s.report(elapsed)
    return 0


# --------------------------------------------------------------------------
# diag: warum bleibt das Panel schwarz?
# --------------------------------------------------------------------------

def _play_file(path, fps, brightness, chunk, seconds, label):
    """Eine Datei fuer `seconds` Sekunden in Chunks der Groesse `chunk` senden."""
    size = os.path.getsize(path)
    p = Panel(verbose=False)
    s = VideoSession(p, fps=fps, brightness=brightness, verbose=False)
    s.start()
    print(f"    {label}")
    print(f"    Datei {os.path.basename(path)} ({size/1e6:.2f} MB), "
          f"Chunk {chunk}, {fps} fps")
    t0 = time.perf_counter()
    try:
        while time.perf_counter() - t0 < seconds:
            with open(path, "rb") as f:
                while time.perf_counter() - t0 < seconds:
                    data = f.read(chunk)
                    if not data:
                        break
                    if not s.send_chunk(data, last=(last_flag and f.tell() >= size)):
                        print("    [err ] Chunk abgelehnt")
                        return
    except KeyboardInterrupt:
        pass
    finally:
        s.stop()
        print(f"    {s.sent_chunks} Chunks, {s.sent_bytes/1e6:.2f} MB, "
              f"{s.waits}x gebremst")
        p.close()          # sonst scheitert die naechste Variante mit Errno 13


def _live(seconds, fps, brightness, rot, min_chunk, threads, label):
    import panel_bench as B
    cmd = ffmpeg_cmd("x264", fps, 20, "12M", threads)
    proc = subprocess.Popen(cmd, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                            stderr=subprocess.DEVNULL, bufsize=0,
                            creationflags=OHNE_FENSTER)
    q = queue.Queue()
    threading.Thread(target=lambda: ([q.put(x) for x in iter(
        lambda: proc.stdout.read(65536), b"")], q.put(None)), daemon=True).start()
    p = Panel(verbose=False)
    s = VideoSession(p, fps=fps, brightness=brightness, verbose=False)
    s.start()
    print(f"    {label}")
    print(f"    live, Chunk ab {min_chunk} Byte, {fps} fps")
    pending = bytearray()
    frames = 0
    t0 = time.perf_counter()
    try:
        while time.perf_counter() - t0 < seconds:
            tgt = t0 + frames / fps
            now = time.perf_counter()
            if now < tgt:
                time.sleep(tgt - now)
            proc.stdin.write(to_wire(B.render_bench_frame(frames / fps), rot)
                             .convert("RGB").tobytes())
            frames += 1
            while True:
                try:
                    b = q.get_nowait()
                except queue.Empty:
                    break
                if b is None:
                    raise RuntimeError("ffmpeg beendet")
                pending += b
            if len(pending) >= min_chunk:
                s.send_chunk(bytes(pending))
                pending.clear()
    except (KeyboardInterrupt, BrokenPipeError, RuntimeError):
        pass
    finally:
        try:
            proc.stdin.close()
        except Exception:
            pass
        proc.terminate()
        s.stop()
        print(f"    {frames} Frames, {s.sent_chunks} Chunks, "
              f"{s.sent_bytes/1e6:.2f} MB, {s.waits}x gebremst")
        p.close()


def do_vdiag(seconds, brightness, rot):
    """Vier Varianten nacheinander — welche zeigt ein Bild?"""
    ref = os.path.join(INSTALL_DIR, "video", "4801920", "AMD.mp4.h264")
    eigen = os.path.join(os.path.dirname(__file__), "..", "captures",
                         "unser_stream.h264")
    eigen = os.path.abspath(eigen)

    schritte = []
    if os.path.exists(ref):
        schritte.append(("1", lambda: _play_file(ref, 25, brightness, DEFAULT_CHUNK,
            seconds, "Referenzdatei, volle Chunks, 25 fps — exakt das Verfahren der Bibliothek")))
        schritte.append(("2", lambda: _play_file(ref, 24, brightness, DEFAULT_CHUNK,
            seconds, "Referenzdatei, volle Chunks, 24 fps — die Datei ist 24 fps")))
    else:
        print(f"[warn] Referenzdatei fehlt: {ref}")
    if os.path.exists(eigen):
        schritte.append(("3", lambda: _play_file(eigen, 25, brightness, DEFAULT_CHUNK,
            seconds, "UNSER Strom als Datei, volle Chunks — trennt Encoder von Live-Pfad")))
    else:
        print(f"[hinweis] {eigen} fehlt — Schritt 3 entfaellt")
    schritte.append(("4", lambda: _live(seconds, 25, brightness, rot, DEFAULT_CHUNK, 1,
        "live, aber auf volle 202752er Chunks gepuffert")))
    schritte.append(("5", lambda: _live(seconds, 25, brightness, rot, 8192, 1,
        "live, kleine Chunks — der bisherige Stand, bekannt: kein Bild")))

    print()
    print(f"Jede Variante laeuft {seconds:.0f} s. Schau aufs Panel und merk dir,")
    print("bei welcher Nummer ein Bild kommt.")
    print()
    for nr, fn in schritte:
        print(f"--- Variante {nr} ---")
        try:
            fn()
        except Exception as e:
            print(f"    [err ] Variante {nr} abgebrochen: {type(e).__name__}: {e}")
        try:
            input("    Enter fuer die naechste ... ")
        except EOFError:
            pass
        print()
    print("Welche Nummer hat ein Bild gezeigt?")
    print("  1 oder 2  -> Transport und Init sind gut, unser Encoder ist schuld")
    print("  3         -> Encoder gut, der Live-Pfad ist schuld")
    print("  4         -> die Chunkgroesse war das Problem")
    print("  keine     -> die Init-Sequenz passt nicht zu diesem Geraet")
    return 0


# --------------------------------------------------------------------------
# vinit: acht Init-Sequenzen durchprobieren
# --------------------------------------------------------------------------
#
# Die Sequenz der Referenzbibliothek (111,112,13,14,41,102,15,17) bringt auf
# diesem Geraet kein Bild, obwohl TURZX Video-Themes abspielt. Aus dem IL von
# TURZX.exe stammt ein groesseres Vokabular: 12, 38, 39, 40, 42, 51, 52, 98,
# 99, 110, 113, 114, 150, 246, 251, 252. Auffaellig:
#   * TURZX kennt **42**, nicht 41
#   * MethodDef #230 sendet 52 -> 102 -> 123 -> 15 -> 51 -> 102 -> 123,
#     51 und 52 klammern also offenbar einen Moduswechsel
#   * 110, 113, 114 stehen direkt neben 111 und 112

SEQUENZEN = [
    ("1 lib",     "Referenzbibliothek, unveraendert (bekannt: kein Bild)",
     [111, 112, 13, "hell", 41, "clear", "fps", 17]),
    ("2 lib42",   "dieselbe, aber 42 statt 41 — TURZX kennt kein 41",
     [111, 112, 13, "hell", 42, "clear", "fps", 17]),
    ("3 pre110",  "110 vorangestellt",
     [110, 111, 112, 13, "hell", 42, "clear", "fps", 17]),
    ("4 pre113",  "113 vorangestellt",
     [113, 111, 112, 13, "hell", 42, "clear", "fps", 17]),
    ("5 pre114",  "114 vorangestellt",
     [114, 111, 112, 13, "hell", 42, "clear", "fps", 17]),
    ("6 turzx",   "die Sequenz aus MethodDef #230: 52 -> clear -> 123 -> fps -> 51",
     [52, "clear", 123, "fps", 51]),
    ("7 turzx2",  "dieselbe ohne Clear und Stop",
     [52, "fps", 51]),
    ("8 voll",    "alles kombiniert: 52 + 110 + Lib-Sequenz + 51",
     [52, 110, 111, 112, 13, "hell", 42, "clear", "fps", 17, 51]),
]


def _init_seq(p, schritte, fps, brightness, verbose=False):
    """Eine Init-Sequenz abarbeiten. Gibt die gesendeten Kommandos zurueck."""
    gesendet = []
    for s in schritte:
        if s == "hell":
            pkt = build_packet(CMD_BRIGHTNESS); pkt[8] = brightness
            p.cmd(CMD_BRIGHTNESS, packet=pkt) if verbose else p.xfer(seal(pkt))
            gesendet.append(f"14({brightness})")
        elif s == "fps":
            pkt = build_packet(CMD_FRAMERATE); pkt[8] = fps
            p.cmd(CMD_FRAMERATE, packet=pkt) if verbose else p.xfer(seal(pkt))
            gesendet.append(f"15({fps})")
        elif s == "clear":
            png = clear_png()
            pkt = build_packet(CMD_UPLOAD_PNG); put_size_be(pkt, len(png))
            p.xfer(seal(pkt) + png)
            gesendet.append("102")
        else:
            p.xfer(seal(build_packet(int(s))))
            gesendet.append(str(s))
        time.sleep(0.05)
    return gesendet


def do_vinit(seconds, brightness, fps, ab):
    ref = os.path.join(INSTALL_DIR, "video", "4801920", "AMD.mp4.h264")
    if not os.path.exists(ref):
        raise SystemExit(f"Referenzdatei fehlt: {ref}")
    size = os.path.getsize(ref)

    print(f"Acht Init-Sequenzen, je {seconds:.0f} s mit {os.path.basename(ref)}.")
    print("Schau aufs Panel. Sobald EINE ein Bild zeigt, kannst du abbrechen.")
    print()

    for idx, (name, beschreibung, schritte) in enumerate(SEQUENZEN, 1):
        if idx < ab:
            continue
        print(f"--- {name} ---")
        print(f"    {beschreibung}")
        p = None
        try:
            p = Panel(verbose=False)
            p.cmd(CMD_SYNC)
            time.sleep(0.15)
            gesendet = _init_seq(p, schritte, fps, brightness)
            print(f"    Init: {' -> '.join(gesendet)}")
            gesamt = 0
            t0 = time.perf_counter()
            fehler = False
            while time.perf_counter() - t0 < seconds and not fehler:
                with open(ref, "rb") as f:
                    while time.perf_counter() - t0 < seconds:
                        data = f.read(DEFAULT_CHUNK)
                        if not data:
                            break
                        pkt = build_packet(CMD_PLAY_H264)
                        put_size_be(pkt, len(data))
                        if f.tell() >= size:
                            pkt[12] = 1
                        if p.xfer(seal(pkt) + data) is None:
                            print("    [err ] Uebertragung abgebrochen — Panel haengt.")
                            print(f"    USB-Kabel ab und an, dann: vinit --ab {idx + 1}")
                            fehler = True
                            break
                        gesamt += len(data)
                        st = p.xfer(seal(build_packet(CMD_STREAM_STATUS)))
                        if st and len(st) > 8 and st[8] > 3:
                            time.sleep(0.02)
            print(f"    {gesamt/1e6:.2f} MB gesendet")
            p.xfer(seal(build_packet(CMD_STREAM_STOP)))
            if fehler:
                return 1
        except Exception as e:
            print(f"    [err ] {type(e).__name__}: {e}")
            print(f"    USB-Kabel ab und an, dann: vinit --ab {idx + 1}")
            return 1
        finally:
            if p is not None:
                p.close()
        try:
            input("    Enter fuer die naechste ... ")
        except EOFError:
            pass
        print()

    print("Welche Nummer hat ein Bild gezeigt?")
    print("Keine? Dann bringt Raten nichts mehr — dann der USB-Mitschnitt,")
    print("waehrend TURZX das AMD-Theme abspielt. Mit dem DES-Schluessel")
    print("koennen wir jedes Kommando im Klartext mitlesen.")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)

    pl = sub.add_parser("play", help="fertigen Annex-B-Strom abspielen")
    pl.add_argument("path")
    pl.add_argument("--loop", action="store_true")
    pl.add_argument("--fps", type=int, default=25)
    pl.add_argument("--brightness", type=int, default=32, help="Rohwert 0-102")
    pl.add_argument("--no-poll", action="store_true",
                    help="Cmd 122 gar nicht abfragen")
    pl.add_argument("--no-last", action="store_true",
                    help="das Endflag [12]=1 nie setzen")
    pl.add_argument("--rate", type=float, default=170.0,
                    help="Tempobremse in KB/s (0 = ungebremst); TURZX nutzt ~170")

    lv = sub.add_parser("live", help="rendern, encodieren, streamen")
    lv.add_argument("--seconds", type=float, default=20.0)
    lv.add_argument("--fps", type=int, default=25)
    lv.add_argument("--encoder", choices=("x264", "amf"), default="x264")
    lv.add_argument("--crf", type=int, default=20)
    lv.add_argument("--maxrate", default="12M")
    lv.add_argument("--brightness", type=int, default=32)
    lv.add_argument("--rot", type=int, choices=(90, 270), default=DEFAULT_ROT)
    lv.add_argument("--min-chunk", type=int, default=8192,
                    help="ab wie vielen Byte ein Chunk rausgeht")
    lv.add_argument("--no-poll", action="store_true",
                    help="Cmd 122 gar nicht abfragen")
    lv.add_argument("--threads", type=int, default=1,
                    help="x264-Threads; >1 kostet threads-1 Frames Latenz")

    dg = sub.add_parser("vdiag", help="vier Varianten durchprobieren")
    dg.add_argument("--seconds", type=float, default=12.0)
    dg.add_argument("--brightness", type=int, default=60)
    dg.add_argument("--rot", type=int, choices=(90, 270), default=DEFAULT_ROT)

    vi = sub.add_parser("vinit", help="acht Init-Sequenzen durchprobieren")
    vi.add_argument("--seconds", type=float, default=10.0)
    vi.add_argument("--brightness", type=int, default=60)
    vi.add_argument("--fps", type=int, default=25)
    vi.add_argument("--ab", type=int, default=1, help="ab welcher Nummer fortsetzen")

    a = ap.parse_args()
    if a.cmd == "vinit":
        return do_vinit(a.seconds, a.brightness, a.fps, a.ab)
    if a.cmd == "vdiag":
        return do_vdiag(a.seconds, a.brightness, a.rot)
    if a.cmd == "play":
        return do_play(a.path, a.loop, a.brightness, a.fps,
                       not a.no_poll, not a.no_last, a.rate)
    return do_live(a.seconds, a.fps, a.encoder, a.crf, a.maxrate,
                   a.brightness, a.rot, a.min_chunk, a.threads, not a.no_poll)


if __name__ == "__main__":
    sys.exit(main())
