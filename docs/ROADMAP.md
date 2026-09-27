# Roadmap

Milestones are ordered by what makes Libre Panel useful to the most people
first. Each hardware step needs a tester with the panel.

## 0.1 — first public preview

- [x] Theme format, validation, safe sharing
- [x] Renderer with text, metric, bar, gauge, graph, clock, image, weather, rect
- [x] Visual editor: drag & drop, properties, panel menu with rescaling, import/export
- [x] Panel catalog for all Turing/TURZX sizes and compatible brands
- [x] Sensors: psutil, LibreHardwareMonitor, demo; configurable Open-Meteo weather
- [x] TURZX V1.x USB driver, PNG path, with reconnect and simulator tests
- [x] `libre-panel doctor`: hardware check with test cards and report
- [x] Built-in themes: Libre Default, SPUR II, Orbit, Slate, Column
- [ ] `doctor` passed on the 9.2" (then: status "supported") and at least one other V1.x size
- [ ] First release: PyPI + Windows/Linux/macOS downloads (see RELEASING.md)

## 0.2 — smooth panels

- [ ] H.264 video layer for V1.x USB panels (25–50 fps, protocol documented in
      [turzx-usb.md](protocol/turzx-usb.md)); needs `ffmpeg`
- [x] Animations: values glide between readings
- [ ] Smooth scrolling graphs, rolling clock digits
- [x] Glow / soft-shadow style for widgets
- [ ] Standby image when the PC shuts down

## 0.3 — every Turing panel

- [ ] Serial rev. A (3.5", UsbPCMonitor), rev. B (XuanFang), rev. C (2.1", 5",
      8.8" V0.x), rev. D (Kipye), WeAct
- [ ] Partial updates for serial panels (only changed rectangles)
- [x] Automatic panel detection from USB ids (V1.x USB panels)

## 0.4 — for everyone

- [x] Tray app with autostart (Windows `Run` key, XDG autostart, LaunchAgent; systemd user service documented)
- [ ] Installers: Windows setup, Flatpak / AppImage, Homebrew, winget
- [ ] Editor and default labels in several languages
- [ ] Theme gallery (import by link)

## Later / plugins

- [ ] Lian Li LCD driver (separate package, protocol from lian-li-linux)
- [ ] Optional plugins: media "now playing", PresentMon FPS, volume, HWiNFO shared memory
- [ ] NVIDIA sensors without LibreHardwareMonitor (NVML) on Linux

Personal features of SPUR II (see [SPUR2_MIGRATION.md](SPUR2_MIGRATION.md)) are
not on this roadmap; they can come back as plugins if someone wants them.
