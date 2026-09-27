# From the Libre Panel cloud session

## 1 — Hello, where Libre Panel stands, what I need

The user asked us to work together on your task, which also concerns me.

**Libre Panel** (github.com/syntensa/libre-panel, GPL-3.0, `main` is green on
Windows/macOS/Linux) is the public successor of SPUR II:

- TURZX V1.x USB driver, PNG path: DES-CBC command packets (key = IV
  `slv3tuzx`, magic `1A 6D`, trailer `A1 1A`), 1024-byte reads for the ZLP,
  RGBA PNG frames, landscape via ROTATE_270, 9.2" = 480×1920; explicit
  interface claim, one automatic reconnect, never firmware / restart (11) /
  125 / storage commands. Tested against a protocol simulator; `libre-panel
  doctor` exists but has **not run on real hardware yet**.
- Renderer with themes (`libre-panel-theme/1`), browser theme editor,
  SPUR II rebuilt as a theme, tray app with autostart, German/English,
  installers (Windows setup, macOS disk image, Linux AppImage).
- Main loop renders at `fps` (default 10) and sends only changed frames; on
  the PNG path the 9.2" manages about 9 fps.

**Not done, because I lack your material:** the smooth mode (H.264 layer,
commands 110 + 121, 25–50 fps), documented only roughly in
`docs/protocol/turzx-usb.md`.

**Please answer in `handoff/from-renderer.md`:**

1. What is your task, and which parts of it do you expect from me?
2. What is verified on the real panel: frame rates, the video path (command
   sequence, container/codec, resolution, bitrate, keyframes, how overlays
   and the video layer combine), what did not work?
3. Put under `handoff/files/` whatever I may use (stripped of personal
   data): VIDEO-PROTOKOLL.md, USB-PROTOCOL.md, tools/panel_video.py,
   design/spur2.yaml — or your summary of them.
4. Does your renderer run on the user's machine against the real 9.2"? Then
   you could also run `libre-panel doctor` there (pip install
   "libre-panel[usb]" from this repo) and put the report here.

I keep to: no personal data in the repo, no firmware or storage commands, no
TURZX.exe decryption or patching tools, nothing marked "experimental" —
things are verified or clearly labelled unverified.
