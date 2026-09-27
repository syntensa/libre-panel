#!/usr/bin/env bash
# Check the AppImage: start it as autostart does (without and with a tray),
# the autostart entry, and the udev rule.
set -euo pipefail
appimage="$(realpath "$1")"
root="$(cd "$(dirname "$0")/../.." && pwd)"
export APPIMAGE_EXTRACT_AND_RUN=1  # CI and containers have no FUSE

"$appimage" --version

echo "== without a desktop"
env -u DISPLAY python3 "$root/packaging/smoke_test.py" "$appimage" tray --background

if command -v xvfb-run >/dev/null && command -v stalonetray >/dev/null; then
    echo "== with an X11 tray"
    log="$(mktemp)"
    xvfb-run -a bash -c "
        set -o pipefail
        stalonetray >/dev/null 2>&1 &
        sleep 1
        python3 '$root/packaging/smoke_test.py' '$appimage' tray --background | tee '$log'
    "
    grep -q "tray icon ready" "$log"
fi

echo "== autostart entry"
home="$(mktemp -d)"
HOME="$home" XDG_CONFIG_HOME="$home/.config" LIBRE_PANEL_HOME="$home/settings" \
    "$appimage" autostart enable
grep -qx "Exec=$appimage tray --background" "$home/.config/autostart/libre-panel.desktop"

"$appimage" udev-rules | grep -q 'ATTRS{idVendor}=="1cbe"'
echo OK
