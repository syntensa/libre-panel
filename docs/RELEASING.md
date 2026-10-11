# Releasing

Libre Panel ships three ways; one git tag produces all of them.

| Channel | For | Built by |
|---|---|---|
| PyPI (`pipx install libre-panel`) | Linux users, developers | `release.yml` → trusted publishing |
| GitHub Release downloads | everyone, no Python needed | `release.yml`: Windows setup + portable zip, Linux AppImage + tarball |
| Distribution packages (winget, Flathub, AUR) | later | community / follow-up |

## One-time setup

1. **PyPI:** create the project on pypi.org and add a *trusted publisher* for
   this repository, workflow `release.yml`, environment `pypi`. No API token
   is stored in GitHub.
2. **GitHub:** create the environment `pypi` (Settings → Environments), ideally
   with a required reviewer so every upload is approved, and the repository
   variable `PYPI_UPLOAD` = `true` (Settings → Secrets and variables →
   Actions → Variables). Without it, releases skip PyPI.
3. **Code signing (Windows, recommended):** unsigned `.exe` files trigger
   SmartScreen warnings. Free signing for open source is available through
   [SignPath Foundation](https://signpath.org); add the signing step to the
   Windows job once approved.

## Cutting a release

```bash
# 1. set the version in pyproject.toml and src/libre_panel/__init__.py
# 2. CHANGELOG.md: a section "## 0.1.0 — <date>"
git commit -am "Release 0.1.0"
git tag v0.1.0
git push origin main v0.1.0
```

Without git: on github.com, *Releases → Draft a new release*, tag `v0.1.0`
(*Create new tag on publish*, target `main`), then *Publish release*; the
workflow fills that release in.

The tag starts `release.yml`: it checks that the tag matches the version and
that CHANGELOG.md has its section, runs the tests, builds the PyInstaller
bundles and tests each download. Only when all of that passed, it publishes
the GitHub Release with all files, its notes taken from the CHANGELOG
section; a version like `0.2.0rc1` is marked as a pre-release. Then the PyPI
upload, after approval, if it is set up.

Every download is tested on its own system before it is attached:

- the program renders a theme and starts as the background app the way
  autostart starts it, draws frames and quits through the editor API
  (`packaging/smoke_test.py`);
- Windows: the setup installs silently, the start menu entry and autostart
  entry exist, the installed app loads its LibreHardwareMonitor libraries
  (`libre-panel sensors --check-lhm`, which needs no administrator rights),
  runs, and the uninstaller quits a running app and removes files, shortcut
  and autostart (`packaging/windows/test_installer.py`, Inno Setup script
  `packaging/windows/libre-panel.iss`);
- Linux: the AppImage runs without a desktop and on an X11 tray, and its
  autostart entry points at the AppImage file (`packaging/linux/test_appimage.sh`).

**Dry run:** start *Release* by hand (Actions → Release → Run workflow). It
builds and tests everything but publishes nothing; the bundles are attached to
the run as artifacts.

The bundles are built from `packaging/pyinstaller/libre-panel.spec`. The icons
come from `packaging/make_icons.py` (the logo is drawn in
`src/libre_panel/branding.py`); rerun it after changing the logo.

## LibreHardwareMonitor's libraries (Windows)

The Windows bundle carries LibreHardwareMonitor's .NET libraries, so that the
GPU, temperatures, power and fans need no other program. They are not in the
repository: the Windows job runs `packaging/windows/fetch_lhm.py` before
PyInstaller, which downloads the pinned packages from NuGet, checks each against
the SHA-256 written into the script and takes out the .NET Framework build. The
spec bundles the result as `lhm`; the job then loads it from the bundle.

To move to other versions:

1. Read the licence of every package in the new set (the `.nuspec` on
   nuget.org, the project's licence file, a notice in the files), and that of
   anything embedded in LibreHardwareMonitorLib.dll (its
   `THIRD-PARTY-NOTICES.txt`). Do not ship what is unclear or does not go with
   the GPL; update NOTICE.md.
2. Find what the new LibreHardwareMonitorLib's .NET Framework build really refers
   to: its assembly references (read them from the file, e.g. with `dnfile`),
   not only the dependencies in its `.nuspec`. Whatever it asks for beyond the
   .NET Framework itself has to be in the set, in the version it asks for. Change
   `LIBRARIES` in `fetch_lhm.py` and in `libre_panel/sensors/lhm_inprocess.py`
   (the order is the load order).
3. Change the versions in the table, run
   `python packaging/windows/fetch_lhm.py --print-pins` and copy the SHA-256 it
   prints for each package and library into the table. Run the script as the job
   does, and `libre-panel sensors --check-lhm` on a Windows machine.

## Versioning

[Semantic versioning](https://semver.org). The theme format has its own version
(`libre-panel-theme/1`); only a breaking theme change bumps it, and older
formats keep loading.

## Website

`site/index.html` is the project's page on GitHub Pages; `pages.yml`
publishes it with the pictures from `docs/images` whenever either changes on
`main`. It needs Pages turned on once: *Settings → Pages → Source: GitHub
Actions*, then run the *Website* workflow (*Actions → Website → Run
workflow*). The pictures come from `python tools/readme_images.py`.

## Animated pictures

The GIFs of the README and of forum posts, and the small videos of the website, are made by
`python tools/readme_media.py` into `docs/media` (`--list` says what there is). A clip is a
recipe: the panel in video mode in `tools/media_panel.py` (the real main loop, frame by frame on a
clock of its own, so a clip is the same on every run) or the app in a browser in
`tools/media_app.py`. Each clip gives a GIF (960 px wide, 25 frames a second, under 4 MB, and
the run stops if one cannot be made smaller than 8 MB, what a forum takes), an MP4 and a WebM
for the website, and a poster. ffmpeg makes the videos and the best GIFs (without it Pillow writes
the GIFs); the app's clips need `pip install playwright` and `playwright install chromium`
(without them they are skipped, with a word about it).

Make them again for every release, so that they show the release's look and nothing else:

- A clip shows the theme its recipe names (Lumen on the bars, Libre Default on the small panel,
  Studio for the notice). When Libre Panel's own screens change, or to try another:
  `--theme video-mode=NAME`, a built-in theme or a folder with a theme in it; a clip that goes
  from one theme to another takes two names (`--theme theme-tube=lumen,studio`). SPUR II's screen
  is in one clip only, `where-it-started`.
- `--only NAME ...` makes some of them, `--gif-only` the GIFs for a forum, `--bare` leaves the
  device frame out, `--draft` is half the size and half the frames to try a recipe, and
  `--keep-frames DIR` keeps the pictures a clip was made of.
- Only demo values are drawn: the hardware has the names the demo gives it, and nothing of the
  machine that makes the clips is in them. Look at each clip before it goes in: a recipe cannot
  tell whether a screen reads well.

The README links the GIFs. The website's page takes the videos, with the GIF where a browser
cannot play them:

```html
<video autoplay loop muted playsinline poster="media/video-mode.png">
  <source src="media/video-mode.webm" type="video/webm">
  <source src="media/video-mode.mp4" type="video/mp4">
  <img src="media/video-mode.gif" alt="The panel in video mode">
</video>
```

`pages.yml` copies `docs/images/*.png` only: add `docs/media` to it (its `paths` and a `cp` into
`_site/media`) when the page uses the clips.
