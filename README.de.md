<p align="right"><a href="README.md">English</a> · <b>Deutsch</b></p>

<h1 align="center">
  <img src="src/libre_panel/assets/libre-panel.png" alt="" width="64"><br>
  Libre Panel
</h1>

<p align="center">
  <b>Die Werte deines PCs als bewegtes Licht, auf dem kleinen USB-Display im Gehäuse.</b><br>
  Freie Open-Source-Software für Systemmonitor-Panels von TURZX, Turing Smart Screen, WeAct,
  Kipye und kompatible. Windows und Linux, ohne Konto, ohne Cloud.
</p>

<p align="center">
  <a href="https://github.com/syntensa/libre-panel/releases/latest"><img alt="Neuestes Release" src="https://img.shields.io/github/v/release/syntensa/libre-panel?color=2EE6D6&labelColor=0A0E13"></a>
  <a href="https://github.com/syntensa/libre-panel/actions/workflows/ci.yml"><img alt="CI" src="https://github.com/syntensa/libre-panel/actions/workflows/ci.yml/badge.svg"></a>
  <a href="LICENSE"><img alt="Lizenz: GPL-3.0" src="https://img.shields.io/badge/license-GPL--3.0-A3AEFF?labelColor=0A0E13"></a>
  <img alt="Windows und Linux" src="https://img.shields.io/badge/runs_on-Windows%20%C2%B7%20Linux-E0A8FF?labelColor=0A0E13">
</p>

<picture>
  <source media="(prefers-reduced-motion: reduce)" srcset="docs/images/hero.png">
  <img src="docs/media/video-mode.gif" width="100%" alt="Lumen, das eigene Theme von Libre Panel, im Videomodus auf einem 9,2-Zoll-Panel: was gerade läuft, mit Cover, die Uhrzeit mit der Last der letzten Minute als scrollendes Licht, vier drehende Lüfter und leuchtende Ringe für CPU, GPU und RAM, die sich mit der Last füllen">
</picture>

<p align="center">
  <a href="https://github.com/syntensa/libre-panel/releases/latest"><img alt="Download für Windows: Setup" src="https://img.shields.io/badge/Windows-Setup-2EE6D6?style=for-the-badge&labelColor=111820"></a>
  <a href="https://github.com/syntensa/libre-panel/releases/latest"><img alt="Download für Windows: portables Zip" src="https://img.shields.io/badge/Windows-Portables_Zip-2EE6D6?style=for-the-badge&labelColor=111820"></a>
  <a href="https://github.com/syntensa/libre-panel/releases/latest"><img alt="Download für Linux: AppImage" src="https://img.shields.io/badge/Linux-AppImage-2EE6D6?style=for-the-badge&labelColor=111820"></a>
  <br>
  <sub>Windows 10 und 11 (x64) · Linux (x86-64) · alle Dateien und die Release-Notes im
  <a href="https://github.com/syntensa/libre-panel/releases/latest">neuesten Release</a></sub>
</p>

<p align="center">
  <a href="#in-30-sekunden">Rundgang</a> ·
  <a href="#themes">Themes</a> ·
  <a href="#unterstützte-panels">Panels</a> ·
  <a href="#installation-und-erster-start">Installation</a> ·
  <a href="#datenschutz">Datenschutz</a> ·
  <a href="#faq">FAQ</a> ·
  <a href="https://syntensa.github.io/libre-panel/de/">Website</a>
</p>

Panel anstecken, Libre Panel starten: Es zeigt Prozessor, Grafikkarte, Arbeitsspeicher,
Lüfter, Netzwerk, was gerade läuft und das Wetter draußen, in einem Theme, das du auswählst
oder selbst baust. Es läuft unauffällig im Tray und ersetzt die Hersteller-App.

## In 30 Sekunden

### Flüssig im Videomodus

<p>
  <img src="docs/media/close-up-rings.gif" width="49%" alt="Nahaufnahme der Ringe von Lumen für CPU, GPU und RAM: leuchtende Bögen, die sich mit der Last füllen, und Zahlen, die zum nächsten Messwert zählen">
  <img src="docs/media/close-up-curves.gif" width="49%" alt="Nahaufnahme von Verlauf und Lüftern bei Lumen: die leuchtenden Kurven von CPU und GPU scrollen, vier Lüfter drehen schneller, wenn sie hochlaufen">
</p>

TURZX-USB-Panels haben einen Videodecoder, und Libre Panel füttert ihn mit bis zu 50 Bildern
pro Sekunde. Zahlen zählen zum nächsten Messwert, Ringe und Balken gleiten, Verläufe scrollen
mit jedem Bild ein Stück weiter, und Lüfter drehen mit ihrer echten Drehzahl. Werte liegen als
Licht in gefrästen Rillen; Schrift kann facettiertes Metall sein, und feiner Staub treibt in
der Tiefe dahinter. Die Downloads bringen das ffmpeg mit, das er braucht, und er schaltet sich
auf dem 9,2" von selbst ein, sobald der Standby-Clip des Panels benannt ist
([was er braucht](#faq)); alle anderen Panels bekommen Standbilder, gesendet nur dort, wo sich
etwas ändert.

### Karten, die von selbst wechseln

<img src="docs/media/card-ignition.gif" width="100%" alt="Das Kartenfach von Lumen: der laufende Titel macht der Wetter-Karte mit bewegtem Symbol und den nächsten Stunden Platz; dazwischen zündet eine Lichtlinie">

Ein Kartenfach, mehrere Karten: was gerade läuft, mit Cover und dem Symbol des Players; läuft
nichts, das Wetter mit bewegten Symbolen, den nächsten Stunden und dem Regen der nächsten zwei
Stunden; ist beides nicht da, der Tag. Beim Wechsel zündet eine Lichtlinie, die Karte rollt in
sie hinein, und die nächste rollt aus ihr heraus. Mit dem Downloads-Dienst zeigt die
Netzwerk-Karte einen laufenden Download an ihrer Stelle.

### Hinweise

<img src="docs/media/volume-band.gif" width="100%" alt="Die Lautstärketaste im Theme Studio: ein Band rollt von einer leuchtenden Kante herab, seine Zahl zählt hoch und runter, sein Balken gleitet">

Dreh an der Lautstärke, und ein Band rollt von einer leuchtenden Kante herab und ändert sich
mit, solange du drehst. Ein neuer Titel mit Cover, ein Sensor über einer Grenze, die du
festlegst, ein Stick oder Controller angesteckt (Windows): kurze Hinweise in den Farben des
Themes, als Band oder Karte. Wie jeder Dienst sind sie aus, bis du sie einschaltest.

### Spielmodus

<img src="docs/media/game-entrance.gif" width="100%" alt="Ein Spiel startet: der Bildschirm schließt sich zu einer Linie, der Name des Spiels leuchtet in einem Band auf, und der Spielbildschirm öffnet sich mit FPS, 1-%-Low, Frametime, CPU und GPU; am Ende derselbe Weg zurück">

Starte ein Spiel aus deiner eigenen Liste oder deiner Steam-Bibliothek, und der Bildschirm
schließt sich zu einer Linie, der Name des Spiels leuchtet auf, und der Spielbildschirm
öffnet sich: die FPS, die 1-%-Lows und wer sie bremst, gezählt von Intels PresentMon. Nach
einer Runde vergleicht ein optionaler Bericht sie mit deinen letzten zehn.

### Jede Panelgröße

<img src="docs/media/small-panel.gif" width="100%" alt="Libre Default auf einem 3,5-Zoll-Panel: die Uhrzeit, leuchtende Ringe für CPU und Arbeitsspeicher, die sich füllen, CPU-Temperatur, Laufwerk und Netzwerk, ein scrollender CPU-Verlauf und eine kleine Karte in der Ecke, die vom laufenden Titel zum Wetter wechselt">

Vom 0,96"-Stick bis zum 12,3"-Display: Jedes Theme passt auf jedes Panel und ordnet sich
dafür neu an. Libre Default, hier auf einem 3,5"-Panel, ist der Standard auf allen Panels
außer den 8,8"- und 9,2"-Leisten.

### Die App

<img src="docs/media/app-drag-module.gif" width="100%" alt="Gestalten: ein Wetter-Modul wird aus der Bibliothek auf ein 3,5-Zoll-Panel gezogen, breiter gemacht und zeigt beim Wachsen mehr">

**Gestalten**: Bausteine aufs Panel ziehen. Ein Modul passt sich dem Platz an, den du ihm
gibst, und zeigt umso mehr, je größer es ist; 25 davon, von jedem Kern bis zum nächsten
Termin.

<img src="docs/media/app-look.gif" width="100%" alt="Look: ein Klick auf die Farbwelt Neon färbt ganz Lumen um, und ein Testhinweis zeigt, wie Hinweise aussehen werden">

**Look**: sechs Farbwelten und wie Ringe, Balken, Verläufe und Schrift gezeichnet werden
(Licht, Facette, Acryl oder flach), dazu Panel-Licht, Raster, Tiefenstaub und Hinweise,
neben dem Bild des Panels.

<img src="docs/media/app-command-palette.gif" width="100%" alt="Strg+K: die Eingabe neon findet den Look Neon zwischen Themes, Panels und Bausteinen, und Enter setzt ihn für das ganze Panel">

**Strg+K** findet jeden Befehl, jedes Theme, Modul, jeden Sensor und jede Einstellung mit ein
paar Buchstaben. **Einstellungen** schreibt die `config.toml` für dich, Kommentare bleiben
stehen; die Namen von Prozessor, Grafikkarte und Arbeitsspeicher findet Libre Panel selbst,
und du kannst eigene eintippen. **Start** zeigt das Panel auf einen Blick und die Themes, die
dafür gemacht sind, **Live**, wie es läuft.

## Themes

<p align="center"><img src="docs/images/gallery.png" width="100%" alt="Die eigenen Themes von Libre Panel auf ihren Panels: Lumen und Studio auf 9,2-Zoll-Leisten, darunter Slate (5 Zoll), Libre Default (3,5 Zoll), Orbit (rund), Column (3,5 Zoll hochkant) und Pico (0,96 Zoll)"></p>

| Theme | Gemacht für | |
|---|---|---|
| **Lumen** | 8,8"- und 9,2"-Leisten (1920×480); dort der Standard | das eigene Theme von Libre Panel: was läuft, sonst das Wetter, sonst der Tag; die Uhrzeit mit der Last der letzten Minute als scrollendes Licht und den drehenden Lüftern; leuchtende Ringe für CPU, GPU und RAM, benannt nach deiner Hardware, über dem Netzwerk |
| **Studio** | 8,8"- und 9,2"-Leisten | ruhig: eine große Uhr, das Kartenfach und leuchtende Ringe für CPU, RAM und GPU |
| **Libre Default** | 3,5" (480×320); der Standard auf allen anderen Panels | zwei leuchtende Ringe, Temperatur, Laufwerk und Netzwerk, ein scrollender Verlauf; in der Ecke, was läuft, das Wetter oder der Tag |
| **Slate** | 5" (800×480) | vier Karten: CPU, GPU, Arbeitsspeicher und Netzwerk |
| **Orbit** | rund, 2,1" und 2,8" | drei Ringe um eine große Uhr |
| **Column** | 3,5" hochkant | die Uhr, eine Zeile je Ressource und das Netzwerk |
| **Pico** | 0,96" (160×80) | CPU mit Temperatur, und Arbeitsspeicher |

Jedes Theme lässt sich oben in *Gestalten* über den Panel-Knopf auf jedes andere Panel
bringen. Themes sind schlichtes JSON mit ihren Bildern und Schriften und können keinen Code
ausführen ([Theme-Format](docs/THEMES.md)). Eins gebaut, das dir gefällt? Teile es in den
[Discussions](https://github.com/syntensa/libre-panel/discussions). Ebenfalls eingebaut: die
acht Bildschirme von SPUR II als Legacy-Sammlung, siehe [Wo alles begann](#wo-alles-begann).

<details>
<summary><b>Alle 25 Module</b>: die Bausteine eigener Themes</summary>
<br>
<p align="center"><img src="docs/images/module-catalog.png" width="100%" alt="Jedes Modul auf zwei Zellen: Ring, große Zahl, Verlauf, Balken, Temperaturen, CPU-Kerne, Laufwerke, Prozesse, Netzwerk, Netzwerkdetails, Akku, Spiel-FPS; Uhrzeit, Kalender, Analoguhr, Weltuhr, Countdown, Sonne und Mond, Wetter; Musik, Termine, Dashboard, Bild, System und Titel"></p>

Hardware bis zu jedem Kern, jedem Laufwerk und jedem Lüfter; Uhren für jede Zeitzone, ein
Countdown, Sonnenauf- und -untergang und der Mond (berechnet, ohne Internet); was gerade
läuft, deine Kalender (jede `.ics`), Werte aus Home Assistant und MQTT, eine Diashow. Jedes
Modul zeigt umso mehr, je größer es ist, tritt zur Seite, wenn seine Messwerte fehlen, und
kostet nichts, solange kein Theme es zeigt.
</details>

## Unterstützte Panels

| Panel | Größen | Meldet sich als | Stand |
|---|---|---|---|
| Turing / TURZX | rund 2,8", 4,6", 5,2", 8", 8,8", 9,2", 12,3" | USB-Gerät (`1cbe:…`) | 9,2": stundenlang erprobt, als Standbild und als Video mit 50 fps. Andere Größen: dasselbe Protokoll, ungeprüft |
| Turing Smart Screen | rund 2,1", 3,5", 5", 8,8" | virtueller COM-Port | ungeprüft |
| UsbPCMonitor | 3,5", 5", 7" | virtueller COM-Port | ungeprüft |
| XuanFang | 3,5" | virtueller COM-Port | ungeprüft |
| Kipye Qiye | 3,5" | virtueller COM-Port | ungeprüft |
| WeAct Display FS V1 | 0,96", 3,5" | virtueller COM-Port | ungeprüft |

*Ungeprüft* heißt: Der Treiber folgt einem Protokoll, das auf echten Panels erprobt ist, aber
noch niemand hat dieses Modell mit Libre Panel bestätigt. **Du hast eins?** `libre-panel
doctor` prüft es von Anfang bis Ende und schreibt einen Bericht ohne Seriennummern; häng ihn
an einen
[Panel-Bericht](https://github.com/syntensa/libre-panel/issues/new?template=panel_support.yml),
und das Modell gilt für alle als *unterstützt*. Den Videomodus gibt es für die TURZX-USB-Panels;
die anderen haben keinen Videodecoder. Einzelheiten, Rechte unter Linux und die Panels anderer
Marken: [docs/HARDWARE.md](docs/HARDWARE.md) (englisch).

## Installation und erster Start

Aus dem [neuesten Release](https://github.com/syntensa/libre-panel/releases/latest), ohne
Python:

- **Windows 10 und 11:** `libre-panel-…-windows-x64-setup.exe` installiert für alle Benutzer,
  mit Startmenü-Eintrag, auf Wunsch Start bei der Anmeldung und, in angehakten Kästchen, die
  sagen, was jedes ist, dem Sensordienst, dem Treiber PawnIO und dem, was Bildraten in Spielen
  brauchen; Windows fragt einmal nach Administratorrechten, Libre Panel selbst läuft ohne sie.
  Ein portables Zip gibt es auch: Sein *Alle Sensoren freischalten* richtet dasselbe ein.
- **Linux (x86-64):** `Libre_Panel-…-x86_64.AppImage`: ausführbar machen und starten, ohne
  Installation. Ein Tarball gibt es auch.

Die Downloads bringen mit, was die Funktionen ausführen, in einem Ordner `tools` neben dem
Programm: ffmpeg für den Videomodus und unter Windows PresentMon für die FPS von Spielen und
den Installer des PawnIO-Treibers für die Sensoren der CPU. Sonst muss nichts installiert
werden; ffmpeg steht unter der GPL-3.0, die anderen unter MIT und GPL-2.0
([welche Programme](docs/CONFIGURATION.md#programs-that-come-with-the-downloads), und
[NOTICE.md](NOTICE.md) für ihre Quellen).

1. **Hersteller-App beenden.** Sie hält das Panel für sich fest.
2. **Libre Panel starten**, mit angestecktem Panel. Die Windows-Downloads sind noch nicht
   signiert: SmartScreen fragt einmal nach (*Weitere Informationen → Trotzdem ausführen*).
   Unter Linux bietet *Start* an, deinem Benutzer mit einem Klick Zugriff auf das Panel zu geben
   ([was dabei geschieht](docs/CONFIGURATION.md#linux-sensors-and-panel-access)).
3. **Einen Look wählen.** *Start* fragt „Wie soll es aussehen?“ mit den Themes für das
   gefundene Panel: Lumen auf den 8,8"- und 9,2"-Leisten, Libre Default auf den anderen.
4. **Einschalten, was du willst.** In den *Einstellungen*: Wetter und Ort, Videomodus, die
   Dienste (alle aus, bis du sie einschaltest). Das Tray-Symbol hat Theme, Helligkeit, Pause
   und *Mit dem System starten*.

Unter Windows kommen Temperatur und Leistung des Prozessors, die Mainboard-Lüfter und die
Temperaturen von Arbeitsspeicher und Laufwerken über den Sensordienst von Libre Panel, der sie
mit den nötigen Rechten liest ([FAQ](#faq)); die App selbst läuft ohne diese Rechte. Unter Linux
brauchen die Sensoren keine Rechte, außer den Mainboard-Lüftern und der Leistung des
Prozessors: *Start* bietet beides mit einem Klick an ([FAQ](#faq)). Die App
öffnet sich in einem eigenen Fenster (WebView2 unter Windows, WebKitGTK unter Linux); das
AppImage öffnet sie vorerst im Browser
([Einzelheiten](docs/BACKGROUND.md#the-apps-window)).

Mit Python:

```bash
pipx install "libre-panel[usb,serial,tray] @ git+https://github.com/syntensa/libre-panel@v0.5.0"
libre-panel tray                    # die Hintergrund-App mit Tray-Symbol; öffnet ihr Fenster
libre-panel autostart enable        # bei jeder Anmeldung starten
```

<details>
<summary>Weitere Befehle</summary>

```bash
libre-panel                         # Panel und App im Terminal (Strg+C beendet)
libre-panel editor                  # nur die App, ohne Panel (--browser: im Browser)
libre-panel run                     # nur das Panel ansteuern
libre-panel preview lumen -o lumen.png    # ein Theme als Bild rendern
libre-panel models                  # alle bekannten Panels mit Auflösung
libre-panel devices                 # welches Panel ist angeschlossen?
libre-panel doctor                  # das Panel von Anfang bis Ende prüfen, Bericht schreiben
libre-panel sensors                 # jeder Sensor-Schlüssel mit seinem aktuellen Wert
libre-panel config init             # eine kommentierte config.toml schreiben
libre-panel location "Berlin"       # Koordinaten für das Wetter
libre-panel system-setup --linux    # Linux: ein Schritt für Panel-Zugriff und Mainboard-Lüfter
libre-panel udev-rules              # Linux: die Regel, die deinem Benutzer USB-Zugriff gibt
libre-panel quit                    # die Hintergrund-App beenden
```

</details>

## Datenschutz

**Nichts verlässt deinen PC außer dem Wetter, das du einschaltest.** Kein Konto, keine
Telemetrie, keine Update-Abfrage. Die App wird nur für deinen eigenen PC ausgeliefert
(127.0.0.1), und nur an Anfragen mit einem geheimen Token, der bei jedem Start neu ist.

- **Das Wetter** ist aus, bis du einen Ort setzt. Dann gehen nur seine Koordinaten an
  [Open-Meteo](https://open-meteo.com), alle 15 Minuten. Einen Ort, den du per Name suchst,
  bekommt die Ortssuche von Open-Meteo erst, wenn du auf *Suchen* drückst; gespeichert wird
  er auf etwa einen Kilometer genau.
- **Was du selbst anbindest**, spricht mit der Adresse, die du ihm gibst: Kalender (`.ics`),
  Home Assistant, MQTT. Der Ping des Moduls Netzwerkdetails verbindet sich mit 1.1.1.1 (oder
  dem Host, den du einstellst), solange das Modul auf dem Panel ist, und ein Cover, das ein
  Player per Webadresse nennt, wird von dort geladen.
- **Die optionalen Dienste** behalten, was sie lernen, auf diesem PC: Gedächtnis und
  Rundenbericht in einer SQLite-Datei, die du öffnen oder löschen kannst; die Kühlungsanalyse
  liest die Konfiguration von FanControl und schreibt sie nie. Kein Titel, kein Fenstertitel
  und kein Dateiname wird gelesen.
- **Die Namen deiner Hardware** landen nie im Log, und `libre-panel doctor` schreibt keine
  Seriennummern und keine persönlichen Pfade in seinen Bericht.

## FAQ

<details>
<summary><b>Welches Panel habe ich?</b></summary>
<br>

Produktname und Aufkleber auf der Rückseite sagen es meist. Angesteckt listet `libre-panel
devices`, was es sieht: Ein TURZX-USB-Panel meldet sich mit der USB-ID `1cbe:…` und seiner
Größe, die anderen als virtueller COM-Port (`COM3`, `/dev/ttyACM0`). *Start* in der App nennt
das gefundene Panel; unter *Anderes Panel* wählst du selbst. Im Zweifel fragt und prüft
`libre-panel doctor`. Die [Hardware-Liste](docs/HARDWARE.md) kennt jedes Modell.
</details>

<details>
<summary><b>Warum zeigen CPU-Temperatur und -Leistung unter Windows „--“?</b></summary>
<br>

Die Windows-Downloads bringen die Bibliothek von LibreHardwareMonitor mit und lesen die
Grafikkarte und ihre Lüfter ohne weiteres Programm. Temperatur, Leistung und Takt des
Prozessors, die Temperaturen von RAM und Laufwerken und die Mainboard-Lüfter brauchen
**Administratorrechte** und den Treiber **PawnIO** ([pawnio.eu](https://pawnio.eu)). Der
**Sensordienst** von Libre Panel hat beides: Das Setup installiert ihn (das angehakte Kästchen
*Sensordienst*), das portable Zip bietet auf Start *Alle Sensoren freischalten* an (eine Frage
von Windows), und Libre Panel selbst läuft weiter ohne Administratorrechte
([wie das sicher bleibt](docs/BACKGROUND.md#the-sensor-service)). *Einstellungen → Sensoren*
sagt, ob er läuft. Ohne ihn gehen `libre-panel autostart enable --elevated` (als
Administrator) oder ein LibreHardwareMonitor, den du selbst mit Webserver laufen lässt, wie
bisher ([Einzelheiten](docs/CONFIGURATION.md#windows-sensors-gpu-power-fans)).
</details>

<details>
<summary><b>Warum fehlen unter Linux die Lüfter oder die CPU-Leistung?</b></summary>
<br>

Die meisten Sensoren brauchen unter Linux keine Rechte (Prozessor, Grafikkarte, NVMe-Laufwerke,
Speicher). Drei Dinge schon: die **USB-Berechtigung für das Panel**, der **Treiber für den Chip,
der die Mainboard-Lüfter liest** (der Kernel hat ihn, aber niemand lädt ihn) und, nur wenn du sie
willst, die **Leistung des Prozessors** (diese Zähler hält der Kernel seit 2020 von allen außer
root fern). *Start* zeigt eine Karte, wenn eines davon fehlt, und *Einstellungen → Sensoren* hat
sie immer: ein Klick, dein System fragt einmal nach dem Passwort, und die Karte sagt genau, was
geändert wird. In einem Terminal: `libre-panel system-setup --linux` (mit `--rapl` für die
Leistung, mit `--remove` wird alles zurückgenommen). Mehr:
[Linux-Sensoren und Panel-Zugriff](docs/CONFIGURATION.md#linux-sensors-and-panel-access).
</details>

<details>
<summary><b>Wie bekomme ich den flüssigen Videomodus? Brauche ich ffmpeg?</b></summary>
<br>

Den Videomodus gibt es für die TURZX-USB-Panels, die einen Videodecoder haben. Die Windows-
und Linux-Downloads bringen das [ffmpeg](https://ffmpeg.org) mit, das er braucht; du gibst nur
unter *Einstellungen → Panel* den Namen des Standby-Clips des Panels an, so wie ihn die
Hersteller-App zeigt. Auf dem 9,2" schaltet er sich dann von selbst ein; auf den anderen
TURZX-USB-Panels stellst du ihn zum Ausprobieren auf *an*. Mit einer Python-Installation
installierst du ffmpeg selbst (Windows: `winget install ffmpeg`; Linux: das Paket deiner
Distribution) oder nennst es an derselben Stelle. Auf dem Panel wird nichts gespeichert. Die
seriellen Panels bekommen Standbilder, von denen nur die geänderten Teile gesendet werden
([Einzelheiten](docs/CONFIGURATION.md#video)).
</details>

<details>
<summary><b>Brauche ich LibreHardwareMonitor, FanControl oder PresentMon?</b></summary>
<br>

Es braucht kein weiteres Programm: LibreHardwareMonitor und Intels
[PresentMon](https://github.com/GameTechDev/PresentMon) (Windows, MIT-Lizenz) stecken in den
Windows-Downloads. Für die FPS eines Spiels schaltest du *Bildrate im Spiel* unter
*Einstellungen → Sensoren* ein; PresentMon braucht Administratorrechte oder dein Konto in der
Windows-Gruppe „Performance Log Users“, was das Setup erledigen kann
([wie](docs/CONFIGURATION.md#more-sensor-sources)). Die Kühlungsanalyse liest die
Lüfterkurven von FanControl, falls du FanControl nutzt.
</details>

<details>
<summary><b>Kann es meinem Panel schaden?</b></summary>
<br>

Libre Panel sendet nur Anzeigebefehle: Bilder, Helligkeit, Video. Es fasst die Firmware nie
an und speichert nichts auf dem Panel; Firmware-Updates bleiben Sache der Hersteller-App.
</details>

<details>
<summary><b>macOS?</b></summary>
<br>

Seit 0.5 nicht mehr: Unterstützt werden Windows und Linux, und die macOS-Builds früherer
Versionen hat niemand heruntergeladen.
</details>

## Mitmachen

Am meisten hilft gerade ein **Panel-Bericht**: `libre-panel doctor` auf einem Modell, das als
*ungeprüft* gilt, macht es für alle *unterstützt*. Themes sind in den
[Discussions](https://github.com/syntensa/libre-panel/discussions) willkommen; eine Sprache
braucht eine JSON-Datei ([wie](docs/CONFIGURATION.md#language)); Code, Treiber und Plugins
beginnen bei [CONTRIBUTING.md](CONTRIBUTING.md) und der [Plugin-API](docs/PLUGINS.md).
Sicherheitslücken bitte über ein [privates Advisory](SECURITY.md) melden.

**Dokumentation (englisch):** [Panels](docs/HARDWARE.md) ·
[Hintergrund-App und Autostart](docs/BACKGROUND.md) ·
[Konfiguration](docs/CONFIGURATION.md) · [Themes und Sensor-Schlüssel](docs/THEMES.md) ·
[Plugins](docs/PLUGINS.md) · [Architektur](docs/ARCHITECTURE.md) ·
[TURZX-USB-Protokoll](docs/protocol/turzx-usb.md) · [serielle Protokolle](docs/protocol/serial.md) ·
[Roadmap](docs/ROADMAP.md) · [Release](docs/RELEASING.md) · [Changelog](CHANGELOG.md)

## Wo alles begann

Libre Panel ist aus SPUR II entstanden, einem privaten Dashboard für ein 9,2"-Panel an einem
PC. SPUR II hat das USB-Protokoll des Panels und seinen Videomodus auf echter Hardware
erprobt, und seine Ideen (Licht in gefrästen Rillen, die Karten, das Band, die Röhre) gehören
jetzt Libre Panel, für jedes Panel und jeden PC. Seine acht Bildschirme sind als
Legacy-Sammlung *SPUR II* dabei, am Ende von *Start → Alle*
([was daraus übernommen wurde](docs/SPUR2_MIGRATION.md)).

<p align="center"><a href="docs/media/where-it-started.gif"><img src="docs/media/where-it-started.png" width="70%" alt="Der klassische Bildschirm von SPUR II im Videomodus: der Schriftzug SPUR II, Lüfter und Uhr oben, Lastkurven von CPU und GPU und die Bereiche CPU, RAM, GPU und Netzwerk mit Werten als Licht"></a></p>

## Lizenz und Hinweise

Libre Panel ist freie Software unter der [GPL-3.0-or-later](LICENSE). Die eingebauten Themes
haben ihre eigene Lizenz, angegeben in jeder `theme.json`. Es steht auf den Schultern von
[turing-smart-screen-python](https://github.com/mathoudebine/turing-smart-screen-python)
(GPL-3.0), dessen Reverse Engineering diese Panels dokumentiert; die Windows-Downloads bringen
[LibreHardwareMonitor](https://github.com/LibreHardwareMonitor/LibreHardwareMonitor) mit
(MPL-2.0), [PresentMon](https://github.com/GameTechDev/PresentMon) (MIT) und den Installer des
Treibers [PawnIO](https://pawnio.eu) (GPL-2.0-or-later mit Ausnahme), und jeder Download
[ffmpeg](https://ffmpeg.org) (GPL-3.0-or-later); die Schriften sind Barlow, JetBrains Mono und
Liberation Sans (SIL OFL 1.1); Wetterdaten von [Open-Meteo.com](https://open-meteo.com) (CC BY 4.0). Alles in
[NOTICE.md](NOTICE.md).

„TURZX“, „Turing Smart Screen“ und andere Produktnamen sind Marken ihrer Inhaber. Libre Panel
ist ein unabhängiges Projekt und mit ihnen nicht verbunden.
