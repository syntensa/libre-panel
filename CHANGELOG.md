# Changelog

## Unreleased

First preview of Libre Panel.

- Theme format `libre-panel-theme/1`, visual browser editor with panel menu,
  drag & drop, rescaling between panel sizes, import/export, "show on panel"
- Renderer: text, metric, bar, gauge, graph, clock, image, weather, rect;
  color rules; safe format strings
- Sensors: psutil, LibreHardwareMonitor (checked against real output), demo; Open-Meteo weather
  (off by default)
- Panel catalog: all Turing/TURZX sizes from 2.1" to 12.3" plus compatible brands
- Driver for Turing V1.x USB panels (PNG path) with reconnect and clear error
  messages; `libre-panel doctor` checks a panel end to end and writes a report
- The main loop waits for a missing panel and survives unplugging
- Built-in themes: Libre Default, SPUR II
