# Roadmap

Milestones are ordered by what makes Libre Panel useful to the most people
first. Each hardware step needs a tester with the panel.

## 0.8 — one design for every panel, and lighter

Libre Panel's own themes come in a hand-made copy per size: Lumen alone about twenty times,
Arena, Encore and Almanac eight or nine times each. The four of them are laid out for five of
the 28 sizes of the panels it drives (the 9.2", the 12.3", the 5.2", the 2.1" and bars on
end); the 4.6", the 7", the 8", NZXT's round screens and most of BeadaPanel's, Thermalright's
and JONSBO's sizes get High Contrast or a theme stretched to fit. 0.8 makes each theme one
design that is laid out for every panel, and makes the app ask less of the PC while it does.

**The bar: nothing gets worse.** The layouts made by hand today are what every new one is
measured against. A size whose new layout is not at least as good keeps its hand-made one
(as an override of the family), and every change to a theme or a screen comes with pictures
of it before and after, at every size it is made for. A theme someone chose keeps working
under its id, and themes in the 0.7 format, community themes among them, are read as they are.

### 1. Pictures without bands

- [ ] Gradients, glows and shadows dithered where they are made (as the start screens are
      since 0.7.9): no bands on the 12.3" and the other large panels, black stays black, and
      the same pattern in every frame, so that video and JPEG frames differ only where
      something moves
- [ ] Before and after for every built-in theme at its sizes; what dithering costs measured
      (frame time, the size of JPEG frames and of the video stream)

### 2. Lighter

- [ ] Frames that would show nothing new are not made again or sent
- [ ] What does not move (backgrounds, frames, labels) composed once, not in every frame
- [ ] The panel's loop runs lean while the app's window is closed (discussion #4: about
      480 MB on Windows)
- [ ] Targets, measured on the 12.3" in video mode with every core busy (as in a game): at
      most 8 ms a frame (p95; Arena takes 12 to 18 ms today), and well under 200 MB of
      memory on Windows with the window closed

### 3. Themes that fit every panel

- [ ] A theme format with layouts per shape (long bar, very long bar, 16:9, 3:2, square,
      round, on end), from which every size is laid out; a hand-made layout for a size stays
      possible and wins
- [ ] One design system for the built-in themes: colours, type sizes, spacing, cards
- [ ] Lumen first, at all 28 sizes, measured against its hand-made layouts; then Arena,
      Encore, Almanac, Nimbus, Studio and the others
- [ ] A light variant of each family (for white builds)
- [ ] The screens in the same design: Arena's game mode, the round report, alarms, cards,
      the start screens
- [ ] In the editor: a theme at every size side by side, one's own too

### 4. Finding the sensors

- [ ] Every sensor as a tree by device, with live values, a search and "put it on the panel"
      (discussion #38: a WireView Pro II's readings were there under names no one would find)
- [ ] HWiNFO as a sensor source (its shared memory)

### For everyone (needs the maintainer)

- [ ] Signed Windows downloads (SignPath, free for open source)
- [ ] winget; PyPI (`release.yml` is ready for trusted publishing); Flatpak

## Done

- **0.7** — up to four panels at once, a companion screen beside the first; Claude Code and
  Codex on the panel; High Contrast; drivers for JONSBO, Lian Li, Hongtai, VMAX, BeadaPanel,
  Thermalright, Thermaltake, NZXT, Waveshare and udisp panels; ten languages; community
  themes in the app; the panel as a monitor; the start screen in seven looks
- **0.6** — bars on end; Arena, the game mode's screen; an app that leads with the panel
- **0.5** — video mode up to 50 fps; Lumen; an app of its own that designs the panel; SPUR II's
  messages, transitions, game mode and autopilot (see [SPUR2_MIGRATION.md](SPUR2_MIGRATION.md))
- **0.4** — 25 modules: every temperature, core and drive, clocks for any time zone, the sun
  and the moon, what is playing, the calendar, Home Assistant, MQTT, the game's frame rate
- **0.3** — modules on a grid, six looks, an editor where a theme takes two minutes
- **0.2** — Studio; themes that fit each PC; the serial panels (Turing rev. A to D, WeAct)
- **0.1** — theme format, renderer, visual editor, panel catalogue, sensors, the TURZX V1.x
  USB driver, `doctor`, plugins, tray app, Windows setup and AppImage

## Still open

- [ ] `doctor` passed on each serial family and on more V1.x sizes (issue #1: panel reports
      wanted)
