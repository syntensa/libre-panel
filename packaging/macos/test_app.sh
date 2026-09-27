#!/usr/bin/env bash
# Check the disk image: mount it, copy the app out, start it the ways a user's
# Mac does (LaunchAgent at login, double-click), quit it.
set -euo pipefail
dmg="$1"
root="$(cd "$(dirname "$0")/../.." && pwd)"

mnt="$(mktemp -d)"
hdiutil attach "$dmg" -nobrowse -readonly -mountpoint "$mnt"
test -L "$mnt/Applications"
dest="$(mktemp -d)"
cp -R "$mnt/Libre Panel.app" "$dest/"
hdiutil detach "$mnt"
app="$dest/Libre Panel.app"
plist="$app/Contents/Info.plist"

test "$(plutil -extract CFBundleExecutable raw "$plist")" = "LibrePanel"
test "$(plutil -extract LSUIElement raw "$plist")" = "true"   # menu bar only
"$app/Contents/MacOS/libre-panel" --version
codesign -dv "$app/Contents/MacOS/LibrePanel" 2>&1 | grep -i signature || true

echo "== as the LaunchAgent starts it"
python3 "$root/packaging/smoke_test.py" "$app/Contents/MacOS/LibrePanel" tray --background

echo "== the LaunchAgent points at the app"
home="$(mktemp -d)"
HOME="$home" "$app/Contents/MacOS/libre-panel" autostart enable
agent="$home/Library/LaunchAgents/io.github.syntensa.libre-panel.plist"
test "$(plutil -extract ProgramArguments.0 raw "$agent")" = "$app/Contents/MacOS/LibrePanel"
test "$(plutil -extract ProgramArguments.1 raw "$agent")" = "tray"

echo "== opened like a double-click"
cli="$app/Contents/MacOS/libre-panel"
settings="$HOME/Library/Application Support/LibrePanel"
rm -f "$settings/instance.json"
open -n "$app" --args tray --background
for _ in $(seq 1 120); do
    [ -f "$settings/instance.json" ] && break
    sleep 0.5
done
test -f "$settings/instance.json"
url="$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["editor"])' "$settings/instance.json")"
curl -fsS "${url}api/app" | grep -q '"available": true'
"$cli" quit
test ! -f "$settings/instance.json"
echo OK
