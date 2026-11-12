#!/usr/bin/env bash
# Check the AppImage: start it as autostart does (without and with a tray),
# the autostart entry, the udev rule, the ffmpeg it carries, and the system setup as pkexec
# starts it. RUST=1: the AppImage carries the Rust app (the smoke test's Rust run, and
# check_tools.py without the Python app's own look for its programs).
set -euo pipefail
appimage="$(realpath "$1")"
root="$(cd "$(dirname "$0")/../.." && pwd)"
export APPIMAGE_EXTRACT_AND_RUN=1  # CI and containers have no FUSE
smoke=(--tools)
tools=()
if [ -n "${RUST:-}" ]; then
    smoke+=(--rust)
    tools+=(--no-discovery)
fi

"$appimage" --version

echo "== without a desktop"
env -u DISPLAY python3 "$root/packaging/smoke_test.py" "${smoke[@]}" "$appimage" tray --background

if command -v xvfb-run >/dev/null && command -v stalonetray >/dev/null; then
    echo "== with an X11 tray"
    log="$(mktemp)"
    xvfb-run -a bash -c "
        set -o pipefail
        stalonetray >/dev/null 2>&1 &
        sleep 1
        python3 '$root/packaging/smoke_test.py' ${smoke[*]} --tray '$appimage' tray --background | tee '$log'
    "
    grep -q "tray icon ready" "$log"
fi

echo "== the programs it carries: ffmpeg encodes the video mode's stream, the app finds it"
# The AppImage is read-only while it runs: take its files out and check them where they are.
unpacked="$(mktemp -d)"
(cd "$unpacked" && env -u APPIMAGE_EXTRACT_AND_RUN "$appimage" --appimage-extract > /dev/null)
python3 "$root/packaging/check_tools.py" "$unpacked/squashfs-root/usr/lib/libre-panel" "${tools[@]}"
echo "== the C library it needs: no newer than the oldest Ubuntu LTS still supported has"
python3 "$root/packaging/linux/check_glibc.py" "$unpacked/squashfs-root" "${GLIBC_LIMIT:-2.35}"
rm -rf "$unpacked"

echo "== autostart entry"
home="$(mktemp -d)"
HOME="$home" XDG_CONFIG_HOME="$home/.config" LIBRE_PANEL_HOME="$home/settings" \
    "$appimage" autostart enable
grep -qx "Exec=$appimage tray --background" "$home/.config/autostart/libre-panel.desktop"

"$appimage" udev-rules | grep -q 'ATTRS{idVendor}=="1cbe"'

echo "== system setup in the environment pkexec makes"
# pkexec runs the program with a bare environment (a short PATH, no DISPLAY, none of the
# person's variables). The dry run needs no rights: it must still find its way and say what the
# setup would do (or that there is nothing to do).
plan="$(env -i PATH=/usr/sbin:/usr/bin:/sbin:/bin HOME="$home" LC_ALL=C APPIMAGE_EXTRACT_AND_RUN=1 \
    "$appimage" system-setup --linux --dry-run)"
grep -Eq '60-libre-panel\.rules|Nothing to do' <<<"$plan"
echo OK
