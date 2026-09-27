#!/usr/bin/env bash
# Package "dist/Libre Panel.app" as a disk image with a link to Applications.
#   packaging/macos/build_dmg.sh dist/libre-panel-0.1.0-macos-arm64.dmg
set -euo pipefail
out="$1"
staging="$(mktemp -d)"
cp -R "dist/Libre Panel.app" "$staging/"
cp LICENSE "$staging/LICENSE.txt"
ln -s /Applications "$staging/Applications"
hdiutil create -volname "Libre Panel" -srcfolder "$staging" -ov -format UDZO "$out"
rm -rf "$staging"
