# Releasing

Libre Panel ships three ways; one git tag produces all of them.

| Channel | For | Built by |
|---|---|---|
| PyPI (`pipx install libre-panel`) | Linux/macOS users, developers | `release.yml` → trusted publishing |
| GitHub Release downloads | everyone, no Python needed | `release.yml` → PyInstaller on Windows, Linux, macOS |
| Distribution packages (winget, Flathub, AUR, Homebrew) | later | community / follow-up |

## One-time setup

1. **PyPI:** create the project on pypi.org and add a *trusted publisher* for
   this repository, workflow `release.yml`, environment `pypi`. No API token
   is stored in GitHub.
2. **GitHub:** create the environment `pypi` (Settings → Environments), ideally
   with a required reviewer so every upload is approved.
3. **Code signing (Windows, recommended):** unsigned `.exe` files trigger
   SmartScreen warnings. Free signing for open source is available through
   [SignPath Foundation](https://signpath.org); add the signing step to the
   Windows job once approved.
4. **Code signing (macOS):** Gatekeeper blocks unsigned downloads, including
   when the LaunchAgent starts them at login. Until the bundle is signed and
   notarized (needs a paid Apple Developer ID), users run once:
   `xattr -dr com.apple.quarantine libre-panel` in the unpacked folder, or
   install with `pipx` instead.

## Cutting a release

```bash
# 1. bump the version in pyproject.toml and src/libre_panel/__init__.py
# 2. update CHANGELOG.md
git commit -am "Release 0.1.0"
git tag v0.1.0
git push origin main v0.1.0
```

The tag starts `release.yml`: tests, build, PyInstaller bundles, a draft
GitHub Release with all files attached, and the PyPI upload after approval.
Review the draft release notes and publish.

Every bundle is smoke-tested on its own system before it is attached: it
renders a theme, starts the background app the way autostart does
(`LibrePanel.exe` on Windows), waits for frames, and quits it through the
editor API (`packaging/smoke_test.py`).

**Dry run:** start *Release* by hand (Actions → Release → Run workflow). It
builds and tests everything but publishes nothing; the bundles are attached to
the run as artifacts.

The bundles are built from `packaging/pyinstaller/libre-panel.spec`. The icons
come from `packaging/make_icons.py` (the logo is drawn in
`src/libre_panel/branding.py`); rerun it after changing the logo.

## Versioning

[Semantic versioning](https://semver.org). The theme format has its own version
(`libre-panel-theme/1`); only a breaking theme change bumps it, and older
formats keep loading.
