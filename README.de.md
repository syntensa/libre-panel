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
  <img src="docs/media/video-mode.gif" width="100%" alt="Lumen, das eigene Theme von Libre Panel, im Videomodus auf einem 9,2-Zoll-Panel: was gerade läuft, mit Cover, die Uhrzeit mit der Last als vorbeiziehendes Licht, vier drehende Lüfter und leuchtende Ringe für CPU, GPU und RAM, die sich mit der Last füllen">
</picture>

<p align="center">
  <b>Ein Videomodus mit bis zu 50 fps, eigene Bildschirme und ein Editor für deine, bis zu vier Panels
  zugleich mit Companion-Bildschirmen, und alles läuft sofort, ohne weiteres Programm.</b>
</p>

<p align="center">
  <a href="https://github.com/syntensa/libre-panel/releases/latest"><img alt="Download für Windows: Setup" src="https://img.shields.io/badge/Windows-Setup-2EE6D6?style=for-the-badge&labelColor=111820"></a>
  <a href="https://github.com/syntensa/libre-panel/releases/latest"><img alt="Download für Windows: portables Zip" src="https://img.shields.io/badge/Windows-Portables_Zip-2EE6D6?style=for-the-badge&labelColor=111820"></a>
  <a href="https://github.com/syntensa/libre-panel/releases/latest"><img alt="Download für Linux: AppImage" src="https://img.shields.io/badge/Linux-AppImage-2EE6D6?style=for-the-badge&labelColor=111820"></a>
  <br>
  <sub>Windows 10 und 11 (x64) · Linux (x86-64) · alle Dateien und die Release-Notes im
  <a href="https://github.com/syntensa/libre-panel/releases/latest">neuesten Release</a></sub>
</p>

<p align="center">
  <a href="#unterstützte-panels">Panels</a> ·
  <a href="#funktionen">Funktionen</a> ·
  <a href="#mehrere-panels-zugleich">Mehrere Panels</a> ·
  <a href="#in-30-sekunden">Rundgang</a> ·
  <a href="#themes">Themes</a> ·
  <a href="#installation-und-erster-start">Installation</a> ·
  <a href="#sofort-startklar">Sofort startklar</a> ·
  <a href="#datenschutz">Datenschutz</a> ·
  <a href="#faq">FAQ</a> ·
  <a href="#mitmachen">Mitmachen</a> ·
  <a href="https://syntensa.github.io/libre-panel/de/">Website</a>
</p>

Panel anstecken, Libre Panel starten: Es zeigt Prozessor, Grafikkarte, Arbeitsspeicher,
Lüfter, Netzwerk, was gerade läuft und das Wetter draußen, in einem Theme, das du auswählst,
selbst baust oder aus der Community installierst, auf bis zu vier Panels zugleich. Es läuft unauffällig im Tray und ersetzt
die Hersteller-App.

## Unterstützte Panels

### Unterstützt und verifiziert

<p>
  <img alt="Turing / TURZX: 8,8&quot;, 9,2&quot;, 12,3&quot;" src="https://img.shields.io/badge/Turing%20%2F%20TURZX-8%2C8%22%20%C2%B7%209%2C2%22%20%C2%B7%2012%2C3%22-34D399?style=for-the-badge&labelColor=111820">
  <img alt="Turing Smart Screen: 3,5&quot;, 8,8&quot;" src="https://img.shields.io/badge/Turing%20Smart%20Screen-3%2C5%22%20%C2%B7%208%2C8%22-34D399?style=for-the-badge&labelColor=111820">
  <img alt="JONSBO ZC-360: jedes Display ein eigenes Panel" src="https://img.shields.io/badge/JONSBO%20ZC--360-jedes%20Display%20ein%20eigenes%20Panel-34D399?style=for-the-badge&labelColor=111820">
  <img alt="Waveshare USB-Monitor: 2,8&quot;, als USB-Display" src="https://img.shields.io/badge/Waveshare%20USB--Monitor-2%2C8%22%2C%20als%20USB--Display-34D399?style=for-the-badge&labelColor=111820">
</p>

Mit Libre Panel auf echten Panels betrieben, wie ihre Besitzer berichten.

### Unterstützt und unverifiziert

<p>
  <img alt="Turing / TURZX: 1,6&quot;, rund 2,1&quot;, rund 2,8&quot;, 4,6&quot;, 5,2&quot;, 8&quot;" src="https://img.shields.io/badge/Turing%20%2F%20TURZX-1%2C6%22%20%C2%B7%20rund%202%2C1%22%20%C2%B7%20rund%202%2C8%22%20%C2%B7%204%2C6%22%20%C2%B7%205%2C2%22%20%C2%B7%208%22-F5C46B?style=for-the-badge&labelColor=111820">
  <img alt="Turing Smart Screen: rund 2,1&quot;, 5&quot;" src="https://img.shields.io/badge/Turing%20Smart%20Screen-rund%202%2C1%22%20%C2%B7%205%22-F5C46B?style=for-the-badge&labelColor=111820">
  <img alt="UsbPCMonitor: 3,5&quot;, 5&quot;, 7&quot;" src="https://img.shields.io/badge/UsbPCMonitor-3%2C5%22%20%C2%B7%205%22%20%C2%B7%207%22-F5C46B?style=for-the-badge&labelColor=111820">
  <img alt="XuanFang: 3,5&quot;" src="https://img.shields.io/badge/XuanFang-3%2C5%22-F5C46B?style=for-the-badge&labelColor=111820">
  <img alt="Kipye Qiye: 3,5&quot;" src="https://img.shields.io/badge/Kipye%20Qiye-3%2C5%22-F5C46B?style=for-the-badge&labelColor=111820">
  <img alt="WeAct Display FS V1: 0,96&quot;, 3,5&quot;" src="https://img.shields.io/badge/WeAct%20Display%20FS%20V1-0%2C96%22%20%C2%B7%203%2C5%22-F5C46B?style=for-the-badge&labelColor=111820">
  <img alt="JONSBO: ZC-360 als ein Bild, TF3-360SC, DS916, DS339" src="https://img.shields.io/badge/JONSBO-ZC--360%20als%20ein%20Bild%20%C2%B7%20TF3--360SC%20%C2%B7%20DS916%20%C2%B7%20DS339-F5C46B?style=for-the-badge&labelColor=111820">
  <img alt="VMAX: 4,6&quot;" src="https://img.shields.io/badge/VMAX-4%2C6%22-F5C46B?style=for-the-badge&labelColor=111820">
  <img alt="Hongtai / Jungle Leopard: Chill Arc 360, MSI EZ Display und weitere" src="https://img.shields.io/badge/Hongtai%20%2F%20Jungle%20Leopard-Chill%20Arc%20360%2C%20MSI%20EZ%20Display%20und%20weitere-F5C46B?style=for-the-badge&labelColor=111820">
  <img alt="Lian Li: Universal Screen, HydroShift II, OLED Curve" src="https://img.shields.io/badge/Lian%20Li-Universal%20Screen%20%C2%B7%20HydroShift%20II%20%C2%B7%20OLED%20Curve-F5C46B?style=for-the-badge&labelColor=111820">
  <img alt="Thermalright: Trofeo Vision" src="https://img.shields.io/badge/Thermalright-Trofeo%20Vision-F5C46B?style=for-the-badge&labelColor=111820">
  <img alt="BeadaPanel: jedes Modell" src="https://img.shields.io/badge/BeadaPanel-jedes%20Modell-F5C46B?style=for-the-badge&labelColor=111820">
  <img alt="NZXT Kraken: mit LCD" src="https://img.shields.io/badge/NZXT%20Kraken-mit%20LCD-F5C46B?style=for-the-badge&labelColor=111820">
  <img alt="Thermaltake: 6&quot;-LCD" src="https://img.shields.io/badge/Thermaltake-6%22--LCD-F5C46B?style=for-the-badge&labelColor=111820">
</p>

Der Treiber folgt einem Protokoll, das auf echten Panels erprobt ist, aber noch
niemand hat das Modell mit Libre Panel bestätigt. **Du hast eins?** Der Panel-Test in
der App (*Live → Diagnose*, oder `libre-panel doctor` im Terminal) prüft es von
Anfang bis Ende und schreibt einen Bericht ohne Seriennummern; ein Klick öffnet einen
[Panel-Bericht](https://github.com/syntensa/libre-panel/issues/new?template=panel_support.yml)
damit ausgefüllt, und das Modell gilt für alle als verifiziert.

### Nicht unterstützt

<p>
  <img alt="GUITION: 3,5&quot;" src="https://img.shields.io/badge/GUITION-3%2C5%22-5F6E80?style=for-the-badge&labelColor=111820">
  <img alt="Fuldho: 3,5&quot;" src="https://img.shields.io/badge/Fuldho-3%2C5%22-5F6E80?style=for-the-badge&labelColor=111820">
  <img alt="AX206: Bilderrahmen" src="https://img.shields.io/badge/AX206-Bilderrahmen-5F6E80?style=for-the-badge&labelColor=111820">
  <img alt="Lian Li: TL LCD, Galahad II LCD, Lancool 207" src="https://img.shields.io/badge/Lian%20Li-TL%20LCD%20%C2%B7%20Galahad%20II%20LCD%20%C2%B7%20Lancool%20207-5F6E80?style=for-the-badge&labelColor=111820">
</p>

Für sie gibt es hier noch keinen Treiber.

Was ein Panel kann und was nicht (Bewegung, Helligkeit, Treiber des Herstellers),
nennt *Einstellungen → Panel* unter seinem Namen; alles dazu, mit den USB-IDs und der
Einrichtung: [docs/HARDWARE.md](docs/HARDWARE.md) (englisch).

**Die Panels am virtuellen COM-Port sind langsamer.** Sie sind die ältere Bauart, und Libre
Panel hält ihre Bewegung von selbst ruhig. Für die meiste Bewegung sind die TURZX-USB-Panels
die richtigen.

<details>
<summary>Mehr</summary>
<br>

Libre Panel schickt ihnen ein Bild pro Sekunde, davon nur die geänderten Teile, sodass Zahlen,
Balken und die Uhr mitkommen. Verläufe rücken einmal pro Sekunde weiter, Zahlen wechseln ohne
Gleiten, die Wettersymbole stehen still, Themes wechseln ohne Überblendung, und ein neues Theme
baut sich einen Moment lang auf. *Einstellungen → Panel → Bewegung auf dem Panel* schaltet die
volle Bewegung für ein Panel ein, das mitkommt. Den Videomodus gibt es für die
TURZX-USB-Panels; die anderen haben keinen Videodecoder. Einzelheiten, Rechte unter Linux und
die Panels anderer Marken: [docs/HARDWARE.md](docs/HARDWARE.md) (englisch).
</details>

## Funktionen

<table>
<tr>
<td width="50%" valign="top"><a href="#flüssig-im-videomodus"><img src="docs/images/tile-video-mode.png" width="100%" alt="Die Ringe von Lumen für CPU, GPU und RAM aus der Nähe, mit Temperatur, Leistung und Takt"></a><br>
<b><a href="#flüssig-im-videomodus">Flüssig im Videomodus</a></b><br>
Bis zu 50 Bilder pro Sekunde auf den TURZX-USB-Panels: Zahlen zählen, Ringe gleiten, Lüfter drehen.</td>
<td width="50%" valign="top"><a href="#mehrere-panels-zugleich"><img src="docs/images/tile-panels.png" width="100%" alt="Drei Panels zugleich: ein 12,3-Zoll-Display mit Lumen, eine 9,2-Zoll-Leiste mit Lumen Companion und ein 5,2-Zoll-Panel mit Encore"></a><br>
<b><a href="#mehrere-panels-zugleich">Mehrere Panels zugleich</a></b><br>
Bis zu vier, im selben Takt: Companions zeigen, was das erste weglässt.</td>
</tr>
<tr>
<td width="50%" valign="top"><a href="#karten-die-von-selbst-wechseln"><img src="docs/images/tile-cards.png" width="100%" alt="Das Kartenfach von Lumen mit dem Wetter: jetzt und die nächsten Stunden"></a><br>
<b><a href="#karten-die-von-selbst-wechseln">Karten, die von selbst wechseln</a></b><br>
Was läuft, sonst das Wetter, sonst der Tag, mit einer Lichtlinie dazwischen.</td>
<td width="50%" valign="top"><a href="#hinweise"><img src="docs/images/tile-notices.png" width="100%" alt="Die Lautstärke als Ring über Lumen, an der Stelle des RAM-Rings"></a><br>
<b><a href="#hinweise">Hinweise</a></b><br>
Die Lautstärke in vier Looks, ein neuer Titel, ein Sensor über seiner Grenze, deine Skripte.</td>
</tr>
<tr>
<td width="50%" valign="top"><a href="#spielmodus"><img src="docs/images/tile-game.png" width="100%" alt="Arena, der Spielbildschirm: die FPS groß mit 1-%-Low und Frametime, dazu ihr Verlauf"></a><br>
<b><a href="#spielmodus">Spielmodus</a></b><br>
Ein Auftritt für jedes Spiel, die FPS mit ihren Lows, ein Bericht nach der Runde.</td>
<td width="50%" valign="top"><a href="#bildschirme-für-musik-wetter-und-uhrzeit"><img src="docs/images/tile-screens.png" width="100%" alt="Encore: das Cover groß, Titel, Künstler und Album"></a><br>
<b><a href="#bildschirme-für-musik-wetter-und-uhrzeit">Musik, Wetter und Uhrzeit</a></b><br>
Encore, Nimbus und Almanac, die übernehmen, wenn Musik läuft oder du weg bist.</td>
</tr>
<tr>
<td width="50%" valign="top"><a href="#abfolge"><img src="docs/images/tile-screens-in-turn.png" width="100%" alt="Nimbus öffnet sich aus einer Lichtlinie, wenn ein Bildschirm auf den anderen folgt"></a><br>
<b><a href="#abfolge">Abfolge</a></b><br>
Fünf Minuten dies, zehn Minuten das, und wieder von vorn.</td>
<td width="50%" valign="top"><a href="#der-eigene-startbildschirm-des-panels"><img src="docs/images/tile-start-screen.png" width="100%" alt="Das Bootlogo des Panels im Look von Libre Panel: sein Zeichen und sein Name"></a><br>
<b><a href="#der-eigene-startbildschirm-des-panels">Der eigene Startbildschirm des Panels</a></b><br>
Sein Bootlogo und Standby-Clip im Look von Libre Panel, mit einem Klick.</td>
</tr>
<tr>
<td width="50%" valign="top"><a href="#leisten-hochkant"><img src="docs/images/tile-vertical.png" width="100%" alt="Die Bildschirme von Libre Panel für eine hochkant stehende Leiste, nebeneinander"></a><br>
<b><a href="#leisten-hochkant">Leisten hochkant</a></b><br>
Hochkant montiert? Jeder Bildschirm wird neu angeordnet statt gedreht.</td>
<td width="50%" valign="top"><a href="#was-deine-ki-werkzeuge-gerade-tun"><img src="docs/images/tile-agents.png" width="100%" alt="Agents: Claude Code arbeitet, mit dem verbrauchten Anteil von fünf Stunden und sieben Tagen"></a><br>
<b><a href="#was-deine-ki-werkzeuge-gerade-tun">Was deine KI-Werkzeuge gerade tun</a></b><br>
Claude Code und Codex: arbeitet, wartet auf dich, und die Limits des Abos.</td>
</tr>
<tr>
<td width="50%" valign="top"><a href="#jede-panelgröße"><img src="docs/images/tile-sizes.png" width="100%" alt="Studio auf einer Leiste, dazu Slate, Libre Default, Orbit, Column und Pico auf den kleineren Panels"></a><br>
<b><a href="#jede-panelgröße">Jede Panelgröße</a></b><br>
Vom 0,96"-Stick bis zum 12,3"-Display, auch runde und hochkant.</td>
<td width="50%" valign="top"><a href="#die-app"><img src="docs/images/tile-app.png" width="100%" alt="Die Ansicht Gestalten der App: ein Wetter-Modul auf einem 3,5-Zoll-Panel, daneben die Bausteine"></a><br>
<b><a href="#die-app">Die App</a></b><br>
35 Module aufs Panel ziehen, einen Look wählen, alles mit Strg+K finden.</td>
</tr>
</table>

## Mehrere Panels zugleich

<p align="center"><img src="docs/images/panels-at-once.png" width="100%" alt="Drei Panels zugleich, wie sie stehen: ein 12,3-Zoll-Display mit Lumen (das Wetter, die Uhrzeit mit der Last, die Ringe und das Netzwerk), darunter eine 9,2-Zoll-Leiste mit Lumen Companion (jeder Kern, die Temperaturen mit den Lüftern, die Laufwerke und die Prozesse) und ein 5,2-Zoll-Panel mit Encore (was läuft, die Uhrzeit und die Lautstärke)"></p>

Bis zu vier Panels zugleich, jedes mit eigenem Theme, auf denselben Messwerten und im selben
Takt.

- **Companions, nichts doppelt.** Ein Panel neben Lumen bekommt **Lumen Companion**: jeden
  Kern, die Temperaturen mit drehenden Lüftern, die Laufwerke und die Prozesse, und Lumen lässt
  weg, was sein Companion zeigt. Encore, Almanac und Arena haben auch einen, und ein Companion
  folgt dem ersten Panel ins Spiel.
- **Ein Bild über alle Panels**, jedes zeigt den Teil, wo es steht, und **Szenen**, die mit
  einem Klick auf jedes Panel ein Theme legen.
- **Panel 1 | 2 | 3** im Kopf der App wählt das Panel, das *Live* zeigt und *Gestalten*
  bearbeitet; ein Modul zieht von einem Panel aufs andere.
- **Jedes für sich**: die Helligkeit (oder die des ersten Panels, mit einem Versatz), wie es
  montiert ist, sein Startbildschirm und ein eigener Prozess, sodass zwei Panels im Videomodus
  auf nichts warten.
- **In echter Größe**: *Einstellungen → Panels* stellt sie neben deinen Monitor, und *Panel
  hinzufügen* fragt Schritt für Schritt, welches Panel es ist, wie es montiert ist und was es
  zeigt.

Ein JONSBO ZC-360 hat drei Displays unter seinen Lüftern: Jedes ist ein eigenes Panel, und sie
starten mit Lumen für Prozessor, Grafikkarte und Arbeitsspeicher.

<p align="center"><img src="docs/images/zc360-screens.png" width="70%" alt="Die drei Lüfter-Displays des JONSBO ZC-360, jedes ein eigenes Panel: 1 Lumen CPU, 2 Lumen GPU und 3 Lumen Memory, jedes ein leuchtender Ring neben seinem Verlauf"></p>

Erreicht ein Sensor seine kritische Grenze, pulsiert der Rand jedes Panels rot:

<picture>
  <source media="(prefers-reduced-motion: reduce)" srcset="docs/images/more-panels.png">
  <img src="docs/media/more-panels.gif" width="100%" alt="Zwei 9,2-Zoll-Leisten übereinander: Lumen mit dem Titel, der Uhrzeit und den Ringen, darunter Lumen Companion mit jedem Kern, den Temperaturen mit drehenden Lüftern, den Laufwerken und den Prozessen; eine Last steigt auf beiden im selben Takt, dann wird der Prozessor zu heiß, beide Ränder pulsieren rot und das zweite Panel zeigt High Contrast, bis er abgekühlt ist">
</picture>

<details>
<summary>Mehr</summary>
<br>

Live, das Tray und die Übersicht zeigen jedes Panel, und *Bild aller Panels* speichert sie, wie
sie stehen, zum Teilen. Alles dazu (englisch): [More panels at
once](docs/CONFIGURATION.md#more-panels-at-once) und [Scenes](docs/CONFIGURATION.md#scenes).
</details>

## In 30 Sekunden

### Flüssig im Videomodus

<img src="docs/media/close-up-rings.gif" width="100%" alt="Die Ringe von Lumen für CPU, GPU und RAM aus der Nähe, in doppelter Größe, im Videomodus: leuchtende Bögen füllen sich mit der Last, und die Zahlen gleiten zum nächsten Messwert">

TURZX-USB-Panels haben einen Videodecoder, und Libre Panel füttert ihn mit bis zu 50 Bildern
pro Sekunde: Zahlen zählen zum nächsten Messwert, Ringe und Balken gleiten, Verläufe scrollen
mit jedem Bild ein Stück weiter, und Lüfter drehen mit ihrer echten Drehzahl. Standard sind 30,
die flüssig aussehen und den PC deutlich weniger fordern; 50 sind noch flüssiger, bei etwa zwei
Dritteln mehr Prozessorzeit (Einstellungen → Panel).

<details>
<summary>Mehr</summary>
<br>

Werte liegen als Licht in gefrästen Rillen; Schrift kann facettiertes Metall sein, und feiner
Staub treibt in der Tiefe dahinter. Die Downloads bringen das ffmpeg mit, das er braucht, und er
schaltet sich auf dem 9,2" von selbst ein, sobald der Standby-Clip des Panels benannt ist
([was er braucht](#faq)); alle anderen Panels bekommen Standbilder, gesendet nur dort, wo sich
etwas ändert.

[Das Bild](docs/images/hero.png): Lumen auf einem 9,2-Zoll-Panel: was läuft, mit Cover, die
Uhrzeit mit der Last als Licht, die Lüfter und leuchtende Ringe für CPU, GPU und RAM.
</details>

### Karten, die von selbst wechseln

<img src="docs/media/card-ignition.gif" width="100%" alt="Das Kartenfach von Lumen: der laufende Titel macht der Wetter-Karte mit bewegtem Symbol und den nächsten Stunden Platz; dazwischen zündet eine Lichtlinie">

Ein Kartenfach, mehrere Karten: was gerade läuft, mit Cover und dem Symbol des Players; läuft
nichts, das Wetter; ist beides nicht da, der Tag.

<details>
<summary>Mehr</summary>
<br>

Das Wetter kommt mit bewegten Symbolen, den nächsten Stunden und dem Regen der nächsten zwei
Stunden. Beim Wechsel zündet eine Lichtlinie, die Karte rollt in sie hinein, und die nächste
rollt aus ihr heraus. Mit dem Downloads-Dienst zeigt die Netzwerk-Karte einen laufenden
Download an ihrer Stelle.
</details>

### Hinweise

<img src="docs/media/volume-looks.gif" width="100%" alt="Die Lautstärke über Lumen in ihren vier Looks nacheinander: eine Leiste am unteren Rand, Pegel, die steigen, ein Ring, der sich füllt, mit der Zahl darin, und eine große Zahl in der Mitte">

Dreh an der Lautstärke, und sie erscheint über jedem Screen, im Look deiner Wahl: als *Leiste*
am Rand, als *Pegel*, die steigen, als *Ring*, der sich füllt, oder *Groß*.

<details>
<summary>Mehr</summary>
<br>

Eingeschaltet wird sie mit einem Klick auf *Start*. Ein neuer Titel mit Cover, ein Sensor über
einer Grenze, die du festlegst, ein Stick oder Controller angesteckt (Windows): kurze Hinweise
in den Farben des Themes, als Band oder Karte; ein Bildschirm, der Lautstärke oder Titel selbst
zeigt, bekommt keinen darüber. Auch deine eigenen Skripte und eine Stream-Deck-Taste können
einen zeigen, sobald du es erlaubst: `libre-panel notify "Rendern fertig"` oder eine Adresse
mit Schlüssel, nur von diesem PC aus. Wie jeder Dienst sind sie aus, bis du sie einschaltest.
</details>

### Spielmodus

<img src="docs/media/round-report.gif" width="100%" alt="Nach einem Spiel öffnet sich der Rundenbericht aus einer Lichtlinie: die durchschnittlichen FPS gegen die letzten Runden, 1-%- und 0,1-%-Low, Frametime und längster Frame, wie sehr GPU, CPU oder FPS-Limit gebremst haben, der Anteil der Runde je FPS-Bereich, die Temperaturen über die Runde und die Höchstwerte von Temperatur, Leistung und Grafikspeicher gegen den Schnitt">

Starte ein Spiel, und der Bildschirm schließt sich zu einer Linie, der Name des Spiels leuchtet
auf, und der Spielbildschirm öffnet sich: die FPS, die 1-%-Lows und wer sie bremst. Nach einer
Runde vergleicht ein optionaler Bericht sie mit deinen letzten zehn.

<details>
<summary>Mehr</summary>
<br>

Das Spiel kommt aus deinem Launcher (Steam, Epic, GOG, Ubisoft Connect, EA app, Battle.net,
Rockstar, Amazon, Xbox-App, Heroic oder Lutris) oder aus eigener Liste und eigenen Ordnern, die
FPS zählt Intels PresentMon, und klein in der Ecke steht die Uhrzeit. Seinen Bildschirm wählst du unter
*Gestalten → Seiten*: **Arena**, den eigenen von Libre Panel, oder einen von SPUR II.

[Der Spielbildschirm](docs/images/arena.png): Arena, der Spielscreen: die Bildrate groß mit
ihrem 1-%-Tief, der Frametime und was sie bremst, ihrem Verlauf, CPU und GPU als Instrumente,
jeder Kern und die Temperaturen mit den Lüftern.
</details>

### Bildschirme für Musik, Wetter und Uhrzeit

<img src="docs/media/nimbus.gif" width="100%" alt="Nimbus: das Wetter jetzt mit bewegtem Symbol, die nächsten Stunden, die nächsten sieben Tage mit Regen, der Mond, wie er heute Nacht leuchtet, und eine kleine Uhr">

**Encore** ist für Musik, **Nimbus** fürs Wetter und **Almanac** eine Uhr mit dem Monat; als
Seiten eingeschaltet, übernimmt Encore, solange ein Titel läuft, und Almanac, wenn du weggehst.

<details>
<summary>Mehr</summary>
<br>

**Encore**: das Cover groß und seine Farben hinter dem ganzen Bildschirm, die Uhrzeit mit
rollenden Ziffern und die Lautstärke als Pegel, die steigen, während du drehst.
[Das Bild](docs/images/encore.png): Encore: das Cover groß mit seinen Farben hinter dem ganzen
Screen, Titel, Künstler und Album, die Uhrzeit und die Lautstärke als Pegel.

**Nimbus**: jetzt und die nächsten Stunden, die nächsten Tage mit ihrem Regen, der Mond, wie er
heute Nacht leuchtet, und eine kleine Uhr.

**Almanac**: eine Uhr mit dem Monat, dem heutigen Tag und der Kalenderwoche; **Almanac
Analog** zeigt die Zeit auf einem Zifferblatt, dessen Sekundenzeiger gleitet, tickt oder
durchläuft. ▶ [Der Clip](docs/media/almanac.gif): Almanac: die Uhrzeit groß mit rollenden
Ziffern neben dem Monat; die Röhre öffnet Almanac Analog, ein Zifferblatt mit gleitendem
Sekundenzeiger, und wieder zurück.

Sie kommen auch von selbst: Schalte unter *Gestalten → Seiten* die Seite **Musik** ein, und
Encore übernimmt, solange ein Titel läuft; mit der Seite **Leerlauf** erscheint Almanac, wenn
du vom PC weggehst. ▶ [Der Clip](docs/media/music-page.gif): Die Musikseite: auf Lumen läuft
nichts; ein Titel startet, und Encore öffnet sich von selbst aus einer Lichtlinie, die
Lautstärkepegel steigen und fallen beim Drehen, und wenn der Titel endet, geht das Panel zurück
zu Lumen.
</details>

### Abfolge

<img src="docs/media/screens-in-turn.gif" width="100%" alt="Abfolge: Almanac, dann Nimbus, dann Encore, jeder für seine Zeit, einer nach dem anderen aus einer Lichtlinie geöffnet, und wieder von vorn">

Fünf Minuten Almanac, fünf Minuten Nimbus, zehn Minuten Studio, und wieder von vorn: Unter
**Start** wählst du die Bildschirme und gibst jedem seine Zeit.

<details>
<summary>Mehr</summary>
<br>

Jeder bekommt seine Sekunden, Minuten oder Stunden, und das Panel zeigt sie nacheinander. Ein
Spiel, ein Hinweis oder ein Theme, das ein Dienst anfordert, geht weiterhin vor.
</details>

### Der eigene Startbildschirm des Panels

<img src="docs/media/start-screen.gif" width="100%" alt="Die eigenen Bildschirme des 9,2-Zoll-Panels im Look von Libre Panel: das Startlogo mit Zeichen und Namen von Libre Panel, dann der Standby-Clip, ein Licht, das von der Mitte aus über die Leiste läuft">

TURZX-USB-Panels zeigen beim Einschalten ein eigenes Logo und spielen beim Start des PCs einen
Clip. Libre Panel zeichnet beides in seinem Look und spielt es mit einem Klick aufs Panel.

<details>
<summary>Mehr</summary>
<br>

Beides erscheint, bevor irgendeine App mit dem Panel spricht. Libre Panel macht es passend zur
Größe deines Panels und spielt es unter **Einstellungen → Panel → Startbildschirm** auf, nach
einer Rückfrage; das Werkslogo bleibt auf dem Panel und kommt mit einem weiteren Klick zurück.
</details>

### Leisten hochkant

<img src="docs/images/vertical.png" width="100%" alt="Die Screens von Libre Panel für eine Leiste, die hochkant steht, nebeneinander: Lumen mit dem Titel, der Uhrzeit, der Last, den Lüftern und den Ringen; Studio mit großer Uhr, der Karte und einer Zeile je Ring; Encore mit großem Cover und der Lautstärke als Pegel; Nimbus mit dem Wetter, den nächsten Tagen und dem Mond; Almanac mit den Stunden über den Minuten; Almanac Analog mit Zifferblatt; Arena mit FPS, CPU, GPU, Kernen und Temperaturen; der Rundenbericht in vier Karten untereinander">

Deine Leiste steht hochkant? Jeder Screen erscheint dann in seiner Fassung dafür, neu
angeordnet statt gedreht.

<details>
<summary>Mehr</summary>
<br>

Sag es beim ersten Start, auf *Start* oder unter *Einstellungen → Panel*. Lumen, Studio,
Encore, Nimbus, Almanac, Arena und der Rundenbericht haben je eine, und das eigene Bootlogo und
der Standby-Clip des Panels werden ebenfalls hochkant gezeichnet.
</details>

### Was deine KI-Werkzeuge gerade tun

<img src="docs/images/agents.png" width="100%" alt="Agents auf einer 9,2-Zoll-Leiste: Claude Code arbeitet, mit seinem bewegten Zeichen und dem Werkzeug bei der Arbeit, Codex wartet auf dich, jedes mit dem verbrauchten Anteil von fünf Stunden und sieben Tagen des Abos und wann sie neu beginnen, neben der Uhrzeit">

**Agents** zeigt, was Claude Code und Codex auf diesem PC tun: arbeitet, wartet auf dich oder
ruht, und wie viel von den Limits des Abos verbraucht ist. Auf Lumen kommt die KI-Karte von
selbst, während eines davon arbeitet, und auch die Cloud-Sitzungen von Claude Code kommen an.

<details>
<summary>Mehr</summary>
<br>

Jedes mit einem bewegten Zeichen, dem Werkzeug bei der Arbeit und der Zeit, bis die Limits neu
beginnen. Unter *Einstellungen → Spielmodus und mehr → KI-Aktivität* siehst du, was in den
Einstellungen der Programme ergänzt wird, bevor etwas geschrieben wird, und nimmst es dort
wieder heraus; nichts verlässt diesen PC. Sitzungen auf claude.ai/code melden sich über ein
eigenes Relay, mit nicht mehr als ihrem Zustand ([KI-Aktivität](docs/CONFIGURATION.md#ai-activity)).
</details>

### Jede Panelgröße

<img src="docs/media/small-panel.gif" width="100%" alt="Libre Default auf einem 3,5-Zoll-Panel: die Uhrzeit, leuchtende Ringe für CPU und Arbeitsspeicher, die sich füllen, CPU-Temperatur, Laufwerk und Netzwerk, ein scrollender CPU-Verlauf und eine kleine Karte in der Ecke, die vom laufenden Titel zum Wetter wechselt">

Vom 0,96"-Stick bis zum 12,3"-Display: Jedes Theme passt auf jedes Panel und ordnet sich dafür
neu an. Auf den runden Panels mit 480×480 haben die Screens eigene Versionen, für den Kreis
angelegt:

<img src="docs/images/round-screens.png" width="100%" alt="Die Screens von Libre Panel für die runden Panels mit 480×480, in runden Rahmen: Lumen, Encore, Nimbus, Almanac, Arena und der runde Bericht">

<details>
<summary>Mehr</summary>
<br>

Libre Default, hier auf einem 3,5"-Panel, ist der Standard auf allen Panels außer den 8,8"- und
9,2"-Leisten.
</details>

### Die App

<img src="docs/media/app-drag-module.gif" width="100%" alt="Gestalten: ein Wetter-Modul wird aus der Bibliothek auf ein 3,5-Zoll-Panel gezogen, breiter gemacht und zeigt beim Wachsen mehr">

**Gestalten**: Bausteine aufs Panel ziehen. **Look**: Farbwelten und wie alles gezeichnet wird.
**Strg+K** findet alles.

<details>
<summary>Mehr</summary>
<br>

**Gestalten**: Ein Modul passt sich dem Platz an, den du ihm gibst, und zeigt umso mehr, je
größer es ist; 35 davon, von jedem Kern über den Ping, einen Timer und den Stromverbrauch des
PCs bis zum nächsten Termin.

**Look**: sechs Farbwelten und wie Ringe, Balken, Verläufe und Schrift gezeichnet werden
(Licht, Facette, Acryl oder flach), dazu Panel-Licht, Raster, Tiefenstaub und Hinweise, neben
dem Bild des Panels. ▶ [Der Clip](docs/media/app-look.gif): Look: ein Klick auf die Farbwelt
Neon färbt ganz Lumen um, und ein Testhinweis zeigt, wie Hinweise aussehen werden.

**Strg+K** findet jeden Befehl, jedes Theme, Modul, jeden Sensor und jede Einstellung mit ein
paar Buchstaben. ▶ [Der Clip](docs/media/app-command-palette.gif): Strg+K: die Eingabe neon
findet den Look Neon zwischen Themes, Panels und Bausteinen, und Enter setzt ihn für das ganze
Panel.

**Einstellungen** schreibt die `config.toml` für dich, Kommentare bleiben stehen; die Namen von
Prozessor, Grafikkarte und Arbeitsspeicher findet Libre Panel selbst, und du kannst eigene
eintippen. **Start** zeigt das Panel auf einen Blick und die Themes, die dafür gemacht sind, und
sagt, wenn eine neue Version da ist, sofern du es nachsehen lässt. **Live** zeigt, wie es läuft,
und mit eingeschaltetem Gedächtnis den **Verlauf**: Temperaturen, Last und Leistung über einen
Tag, eine Woche oder einen Monat, dazu die gespielten Spiele. Der **Player** spielt deine
Videos, GIFs und Bilder auf einem Panel statt seines Themes: hineinziehen, eines anklicken,
anhalten, weiterspringen, spulen, immer wieder oder gemischt, und beim Stoppen kommt das Theme
zurück. **Bildschirm**, eine eigene Seite: Ein Panel zeigt statt seines Themes einen
Bildschirm des Computers: einen deiner Monitore, einen Teil davon, ein Fenster oder einen
eigenen Monitor, auf den der Desktop sich erweitert (Windows mit dem quelloffenen Virtual
Display Driver, aus der App eingerichtet; Linux unter X11 und unter Wayland, GNOME und KDE). Ein
TURZX-Panel im Videomodus bekommt ihn direkt als Videostrom. **Tastenkürzel** für den ganzen PC
halten das Panel an, ändern seine Helligkeit oder öffnen die App aus jedem Fenster (Windows,
Linux mit X11) und lassen sich als Ganzes ausschalten. Die App spricht Deutsch, Englisch,
Französisch, Spanisch, Italienisch, Polnisch, Portugiesisch (Brasilien), Russisch, Japanisch
und vereinfachtes Chinesisch.
</details>

## Themes

<p align="center"><img src="docs/images/gallery.png" width="100%" alt="Die eigenen Themes von Libre Panel auf ihren Panels: Lumen und Studio auf 9,2-Zoll-Leisten, darunter Slate (5 Zoll), Libre Default (3,5 Zoll), Orbit (rund), Column (3,5 Zoll hochkant) und Pico (0,96 Zoll)"></p>

| Theme | Gemacht für | |
|---|---|---|
| **Lumen** | 8,8"- und 9,2"-Leisten (1920×480); dort der Standard | was läuft, die Uhrzeit mit der Last als Licht, leuchtende Ringe, benannt nach deiner Hardware |
| **Studio** | 8,8"- und 9,2"-Leisten | ruhig: eine große Uhr, das Kartenfach und leuchtende Ringe |
| **Encore** | 8,8"- und 9,2"-Leisten | für Musik: das Cover groß, seine Farben hinter dem ganzen Bildschirm |
| **Nimbus** | 8,8"- und 9,2"-Leisten | fürs Wetter: jetzt, die nächsten Stunden und Tage, der Mond |
| **Almanac** | 8,8"- und 9,2"-Leisten | eine Uhr mit dem Monat; **Almanac Analog** auf einem Zifferblatt |
| **Arena** | 8,8"- und 9,2"-Leisten | für den Spielmodus: die FPS groß, CPU und GPU, jeder Kern |
| **Agents** | 8,8"- und 9,2"-Leisten, 12,3" | was Claude Code und Codex tun; **Agents Claude** für Claude Code allein |
| **Libre Default** | 3,5" (480×320); der Standard auf allen anderen Panels | zwei leuchtende Ringe, ein scrollender Verlauf, eine Karte in der Ecke |
| **Slate** | 5" (800×480) | vier Karten: CPU, GPU, Arbeitsspeicher und Netzwerk |
| **Orbit** | rund, 2,1" und 2,8" | drei Ringe um eine große Uhr |
| **Column** | 3,5" hochkant | die Uhr, eine Zeile je Ressource und das Netzwerk |
| **Pico** | 0,96" (160×80) | CPU mit Temperatur, und Arbeitsspeicher |
| **Companions** | ein Panel neben dem ersten, 9,2" in beiden Richtungen und 12,3" | Lumen, Arena, Encore und Almanac Companion: was das erste Panel weglässt |
| **High Contrast** | jede Größe von 2,1" bis 12,3", auch hochkant | Weiß auf Schwarz in sehr großen Ziffern, auch von weitem lesbar |

Was jedes zeigt, ausführlich: [alle eingebauten Themes](docs/THEMES.md#every-built-in-theme)
(englisch). Jedes Theme lässt sich oben in *Gestalten* über den Panel-Knopf auf jedes andere
Panel bringen. Themes sind schlichtes JSON mit ihren Bildern und Schriften und können keinen
Code ausführen ([Theme-Format](docs/THEMES.md)).

### Themes der Community

Themes, die andere Nutzer von Libre Panel gebaut haben, einen Klick entfernt: *Start → Themes →
Themes der Community* zeigt zuerst die, die für das oben gewählte Panel gemacht sind, markiert
mit *Passt*, und die übrigen unter *Alle Größen*. **Installieren** legt eins zu deinen, **Auf
dem Panel zeigen** bringt es dorthin; eins für eine andere Größe fragt vorher nach, weil es
skaliert würde.

- **Teile deins mit zwei Klicks.** *Mit allen teilen …* (die Weltkugel auf der Karte deines
  Themes) fragt, wie es heißt und was es zeigt, speichert es als eine `.zip` und öffnet das
  ausgefüllte Formular auf GitHub: Datei hineinziehen, abschicken. Eine Prüfung läuft von selbst,
  zeichnet sein Bild und bereitet es für die Liste vor; nach einem Blick darauf steht es in der
  App aller. *Exportieren …* und *Importieren …* geben ein Theme direkt weiter.
- **Sicher gebaut.** Ein Theme ist reines JSON mit seinen Bildern und Schriften und kann keinen
  Code ausführen. Die App holt die Liste und die Zips nur aus diesem Repository, prüft jede Zip
  vor dem Entpacken gegen ihren SHA-256 und die Panelgröße ihres Eintrags, und jedes Theme wird
  angesehen, bevor es in die Liste kommt.
- **Ohne Konto.** Die App fragt GitHub erst nach der Liste, wenn du sie öffnest, und die
  Anfrage enthält nichts über dich oder dein Panel.

Die Galerie ist neu, die ersten Themes darin können deine sein
([Teilen, Schritt für Schritt](community/themes/README.md)). Themes zum Besprechen oder noch
im Werden gehören in die [Discussions](https://github.com/syntensa/libre-panel/discussions).

<details>
<summary><b>Die Bildschirme von SPUR II</b></summary>
<br>

Ebenfalls eingebaut: die acht Bildschirme von SPUR II als Legacy-Sammlung, siehe
[Wo alles begann](#wo-alles-begann).
</details>

<details>
<summary><b>Alle 35 Module</b>: die Bausteine eigener Themes</summary>
<br>
<p align="center"><img src="docs/images/module-catalog.png" width="100%" alt="Jedes Modul auf zwei Zellen: Ring, große Zahl, Verlauf, Balken, Temperaturen, CPU-Kerne, Laufwerke, Prozesse, Netzwerk, Netzwerkdetails, Ping, Akku, Stromverbrauch, USV, Spiel-FPS, sehr große Zahl; Uhrzeit, Kalender, Analoguhr, Weltuhr, Countdown, Timer, Sonne und Mond, Mond, Wetter, die nächsten Tage; Musik, Lautstärke, Mikrofon, Termine, Dashboard, KI-Aktivität, Bild, System und Titel"></p>

Hardware bis zu jedem Kern, jedem Laufwerk und jedem Lüfter, der Ping, der Strom, den der PC
zieht, und was er kostet, eine USV; Uhren für jede Zeitzone, ein Countdown, ein Timer,
Sonnenauf- und -untergang und der Mond (berechnet, ohne Internet); was gerade läuft, ob das
Mikrofon stumm ist, deine Kalender (jede `.ics`), Werte aus Home Assistant, MQTT und JSON, was
Claude Code und Codex tun, eine Diashow. Jedes Modul zeigt umso mehr, je größer es ist, tritt
zur Seite, wenn seine Messwerte fehlen, und kostet nichts, solange kein Theme es zeigt.
</details>

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

Die App öffnet sich in einem eigenen Fenster (WebView2 unter Windows, WebKitGTK unter Linux);
das AppImage öffnet sie vorerst im Browser
([Einzelheiten](docs/BACKGROUND.md#the-apps-window)).

### Sofort startklar

**Alles funktioniert sofort, out of the box.** Sensoren, Lüfter, Grafikkarte, Bildraten in
Spielen (Windows) und der Videomodus brauchen kein weiteres Programm, und es muss
nichts weiter heruntergeladen oder installiert werden. Die Downloads bringen die Programme mit,
die die Funktionen ausführen, in einem Ordner `tools` neben dem Programm: ffmpeg für den
Videomodus (Windows und Linux) und unter Windows PresentMon für die Bildraten in Spielen sowie
der Installer des PawnIO-Treibers. Den Rest richtet ein einziger Schritt ein: unter Windows das
Setup, beim portablen Zip und unter Linux ein Klick auf eine Karte in der App. Gefragt wird einmal: unter
Windows nach Administratorrechten, unter Linux nach deinem Passwort; Libre Panel selbst läuft
ohne Administratorrechte.

<details>
<summary><b>Was jeder Download einrichtet, und warum</b></summary>
<br>

| | Windows-Setup | Portables Zip | Linux (AppImage) |
|---|---|---|---|
| **Sensoren von Prozessor, Mainboard, Arbeitsspeicher und Laufwerken** | der Sensordienst und der PawnIO-Treiber, zwei angehakte Kästchen | *Alle Sensoren freischalten* auf Start und unter *Einstellungen → Sensoren* richtet dasselbe ein | Prozessor und NVMe-Laufwerke werden ohne Rechte gelesen; *Sensoren und Panel-Zugriff einrichten* lädt die Kerneltreiber für den Lüfterchip des Boards und für SATA-Laufwerke |
| **Leistung des Prozessors** | liest der Sensordienst | ebenso | ein Schalter in dieser Karte, aus, bis du ihn einschaltest |
| **Grafikkarte** | eingebaut (LibreHardwareMonitor) | eingebaut | AMD, Intel und NVIDIA, ohne Rechte gelesen |
| **Bildraten in Spielen** | PresentMon steckt im Download, mit eigenem Kästchen (abgewählt sagt das Setup vorher, was dann nicht geht); das dritte Kästchen der Sensoren nimmt dich in die Gruppe *Leistungsprotokollbenutzer* auf | PresentMon steckt im Download; *Alle Sensoren freischalten* nimmt dich auf | nur unter Windows |
| **Videomodus** | ffmpeg steckt im Download | ffmpeg steckt im Download | ffmpeg steckt im Download |
| **USB-Zugriff aufs Panel** | nichts zu tun | nichts zu tun | dieselbe Karte schreibt die udev-Regel |
| **Einstellungen** | in deinem eigenen Ordner | im Ordner `data` neben dem Programm: Sie ziehen mit um | in deinem eigenen Ordner |

- **Einmal gefragt, dann läuft es.** Windows fragt einmal nach Administratorrechten, beim Setup
  oder bei *Alle Sensoren freischalten*; unter Linux fragt dein System einmal nach dem
  Passwort. *Nur für mich installieren* geht weiterhin: Das Setup installiert dann ohne diese
  Rechte und überlässt die Systemteile *Alle Sensoren freischalten*. Die Werte kommen in wenigen
  Sekunden; Bildraten in Spielen zählen ab deiner nächsten Anmeldung, wenn Windows die neue
  Gruppenmitgliedschaft übernimmt.
- **Nur lesend.** Der Sensordienst liest nur: Er regelt keinen Lüfter und ändert keine
  Einstellung der Hardware. Wer am PC angemeldet ist, kann seine Werte lesen; Netzwerk-Anmeldungen
  und andere Computer werden abgewiesen
  ([wie er gebaut ist](docs/BACKGROUND.md#the-sensor-service), englisch). *Entfernen* unter
  *Einstellungen → Sensoren*, die Deinstallation und unter Linux *Wieder entfernen* nehmen alles
  zurück. Bei jedem Release installiert der Build das Windows-Setup auf einem Windows-Testrechner,
  prüft, dass der Dienst läuft und Libre Panel durch ihn liest, und deinstalliert es wieder; das
  Setup des AppImage läuft als Probelauf in der schmalen Umgebung, die `pkexec` ihm mitgibt.
- **Der Videomodus** schaltet sich auf dem 9,2" von selbst ein, sobald der Standby-Clip des
  Panels benannt ist; *Start* fragt einmal danach ([was er braucht](#faq)).
- **Die Programme** sind genau die Dateien, die ihre Projekte veröffentlichen. Beim Bau des
  Downloads wird jede gegen eine festgeschriebene SHA-256-Prüfsumme geprüft. ffmpeg steht unter
  der GPL-3.0, PresentMon unter MIT, der Treiber PawnIO unter der GPL-2.0-or-later mit Ausnahme
  ([welche Programme](docs/CONFIGURATION.md#programs-that-come-with-the-downloads); ihre Lizenzen
  und Quellen stehen in [NOTICE.md](NOTICE.md)).
</details>

### Mit Python

```bash
pipx install "libre-panel[usb,serial,tray] @ git+https://github.com/syntensa/libre-panel@v0.7.0"
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

**Nichts verlässt deinen PC, was du nicht selbst eingeschaltet hast.** Kein Konto, keine
Telemetrie. Die App wird nur für deinen eigenen PC ausgeliefert (127.0.0.1), und nur an
Anfragen mit einem geheimen Token, der bei jedem Start neu ist.

<details>
<summary><b>Was wohin geht</b>, Punkt für Punkt</summary>
<br>

- **Das Wetter** ist aus, bis du einen Ort setzt. Dann gehen nur seine Koordinaten an
  [Open-Meteo](https://open-meteo.com), alle 15 Minuten. Einen Ort, den du per Name suchst,
  bekommt die Ortssuche von Open-Meteo erst, wenn du auf *Suchen* drückst; gespeichert wird
  er auf etwa einen Kilometer genau.
- **Neue Versionen**: *Start* fragt einmal, ob nachgesehen werden soll. Sagst du ja, fragt
  Libre Panel einmal am Tag bei GitHub nach der neuesten Version; die Anfrage trägt nur deine
  Versionsnummer, nichts über dich oder dein Panel. *Einstellungen → Start und System* schaltet
  es wieder aus.
- **Hinweise von anderen Programmen** sind aus, bis du sie erlaubst, und kommen dann nur von
  diesem PC und nur mit dem Schlüssel, den Libre Panel dafür erstellt hat.
- **Was du selbst anbindest**, spricht mit der Adresse, die du ihm gibst: Kalender (`.ics`),
  Home Assistant, MQTT, eine JSON-Adresse, der upsd einer USV. Der Ping verbindet sich mit
  1.1.1.1 (oder den Hosts, die du einstellst), solange ein Theme ihn zeigt, und ein Cover, das ein
  Player per Webadresse nennt, wird von dort geladen.
- **Die optionalen Dienste** behalten, was sie lernen, auf diesem PC: Gedächtnis und
  Rundenbericht in einer SQLite-Datei, die du öffnen oder löschen kannst; die Kühlungsanalyse
  liest die Konfiguration von FanControl und schreibt sie nie. Kein Titel, kein Fenstertitel
  und kein Dateiname wird gelesen.
- **Die KI-Aktivität** ist aus, bis du sie einschaltest und Claude Code oder Codex einrichtest.
  Ihre Hooks geben, was sie tun, nur an Libre Panel auf diesem PC weiter, mit einem eigenen
  Schlüssel; für die Nutzung des Abos fragt Libre Panel die Programme, die mit deiner Anmeldung
  bei ihren eigenen Servern nachfragen. Die Cloud-Sitzungen von Claude Code schicken, wenn du
  sie einrichtest, ihren Zustand (nie einen Prompt) an ein ntfy-Topic mit einer langen
  Zufallsadresse, das Libre Panel liest.
- **Die Namen deiner Hardware** landen nie im Log, und `libre-panel doctor` schreibt keine
  Seriennummern und keine persönlichen Pfade in seinen Bericht.
</details>

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
**Administratorrechte** und den Treiber **PawnIO**; dessen Installer steckt im Download. Der
**Sensordienst** von Libre Panel hat beides, und das Setup richtet die beiden ein (die
angehakten Kästchen *Sensordienst* und *PawnIO-Treiber*): Nach dem Setup ist nichts mehr zu
tun. „--“ siehst du nur, wo dieser Teil nicht eingerichtet wurde, also nach *Nur für mich
installieren* oder beim portablen Zip. Dann richtet *Alle Sensoren freischalten* auf Start oder
unter *Einstellungen → Sensoren* ihn mit einer Frage von Windows ein, und Libre Panel selbst
läuft weiter ohne Administratorrechte
([wie das sicher bleibt](docs/BACKGROUND.md#the-sensor-service)). *Einstellungen → Sensoren*
sagt, ob der Dienst läuft. Bei einer Python-Installation gibt es keinen Dienst: Installiere
PawnIO von [pawnio.eu](https://pawnio.eu) und nimm `libre-panel autostart enable --elevated`
(als Administrator) oder ein LibreHardwareMonitor, den du selbst mit Webserver laufen lässt
([Einzelheiten](docs/CONFIGURATION.md#windows-sensors-gpu-power-fans)).
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
Leistung, mit `--remove` wird alles zurückgenommen). Ein Board, für dessen Lüfterchip der Kernel
keinen Treiber hat (manche MSI- und Gigabyte-Boards), braucht einen von außerhalb des Kernels,
den Libre Panel nicht installiert. Mehr:
[Linux-Sensoren und Panel-Zugriff](docs/CONFIGURATION.md#linux-sensors-and-panel-access).
</details>

<details>
<summary><b>Wie bekomme ich den flüssigen Videomodus? Brauche ich ffmpeg?</b></summary>
<br>

Den Videomodus gibt es für die TURZX-USB-Panels, die einen Videodecoder haben. Die Windows-
und Linux-Downloads bringen das [ffmpeg](https://ffmpeg.org) mit, das er braucht; du gibst nur
unter *Einstellungen → Panel* den Namen des Standby-Clips des Panels an, so wie ihn die
Hersteller-App zeigt, oder spielst unter *Einstellungen → Panel → Startbildschirm* den
Standby-Clip von Libre Panel auf, der ihn gleich mit einträgt. Auf dem 9,2" schaltet er sich
dann von selbst ein; auf den anderen TURZX-USB-Panels stellst du ihn zum Ausprobieren auf
*an*. Mit einer Python-Installation installierst du ffmpeg selbst (Windows: `winget install
ffmpeg`; Linux: das Paket deiner Distribution) oder nennst es an derselben Stelle. Der
Videomodus speichert nichts auf dem Panel; nur der Startbildschirm schreibt darauf, auf einen
Klick. Die seriellen Panels bekommen Standbilder, von denen nur die geänderten Teile gesendet
werden ([Einzelheiten](docs/CONFIGURATION.md#video)).
</details>

<details>
<summary><b>Brauche ich LibreHardwareMonitor, FanControl oder PresentMon?</b></summary>
<br>

Es braucht kein weiteres Programm: LibreHardwareMonitor und Intels
[PresentMon](https://github.com/GameTechDev/PresentMon) (Windows, MIT-Lizenz) stecken in den
Windows-Downloads. Die Quelle *Bildrate im Spiel* ist unter Windows von Anfang an an und zählt
die Bilder eines Spiels, solange ein Theme sie zeigt. PresentMon braucht Administratorrechte
oder dein Konto in der Windows-Gruppe „Leistungsprotokollbenutzer“ („Performance Log Users“);
das dritte Kästchen des Setups (oder *Alle Sensoren freischalten*) nimmt dich auf, ab der
nächsten Anmeldung ([wie](docs/CONFIGURATION.md#more-sensor-sources)). Die Kühlungsanalyse liest
die Lüfterkurven von FanControl, falls du FanControl nutzt.
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
PC, das das USB-Protokoll des Panels und seinen Videomodus auf echter Hardware erprobt hat.
Seine Ideen (Licht in gefrästen Rillen, die Karten, das Band, die Röhre) gehören jetzt Libre
Panel, für jedes Panel und jeden PC, und seine acht Bildschirme sind als Legacy-Sammlung *SPUR
II* dabei, am Ende von *Start → Alle* ([was daraus übernommen wurde](docs/SPUR2_MIGRATION.md)).

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
