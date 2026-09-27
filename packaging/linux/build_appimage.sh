#!/usr/bin/env bash
# Build the AppImage from the PyInstaller folder dist/libre-panel.
#   packaging/linux/build_appimage.sh 0.1.0 dist/Libre_Panel-0.1.0-x86_64.AppImage
set -euo pipefail
version="$1"
out="$2"
here="$(cd "$(dirname "$0")" && pwd)"
root="$(cd "$here/../.." && pwd)"
appdir="$root/build/AppDir"
tool="$root/build/appimagetool-x86_64.AppImage"

rm -rf "$appdir"
mkdir -p "$appdir/usr/lib" "$appdir/usr/share/applications" \
         "$appdir/usr/share/icons/hicolor/256x256/apps"
cp -a "$root/dist/libre-panel" "$appdir/usr/lib/libre-panel"
install -m 755 "$here/AppRun" "$appdir/AppRun"
install -m 644 "$here/libre-panel.desktop" "$appdir/libre-panel.desktop"
install -m 644 "$here/libre-panel.desktop" "$appdir/usr/share/applications/libre-panel.desktop"
install -m 644 "$root/src/libre_panel/assets/libre-panel.png" "$appdir/libre-panel.png"
install -m 644 "$root/src/libre_panel/assets/libre-panel.png" \
               "$appdir/usr/share/icons/hicolor/256x256/apps/libre-panel.png"
ln -sf libre-panel.png "$appdir/.DirIcon"

if [ ! -x "$tool" ]; then
    mkdir -p "$(dirname "$tool")"
    curl -fsSL -o "$tool" \
        https://github.com/AppImage/appimagetool/releases/download/continuous/appimagetool-x86_64.AppImage
    chmod +x "$tool"
fi
# Runs without FUSE (CI, containers).
ARCH=x86_64 VERSION="$version" APPIMAGE_EXTRACT_AND_RUN=1 "$tool" --no-appstream "$appdir" "$out"
