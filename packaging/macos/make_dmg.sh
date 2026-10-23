#!/usr/bin/env bash
# Build a disk image with the app and an Applications shortcut.
# usage: make_dmg.sh path/to/3EDIC.app out.dmg
set -euo pipefail
APP="$1"; OUT="$2"
STAGE="$(mktemp -d)"
cp -R "$APP" "$STAGE/"
ln -s /Applications "$STAGE/Applications"
mkdir -p "$(dirname "$OUT")"
rm -f "$OUT"
hdiutil create -volname "3EDIC" -srcfolder "$STAGE" -ov -format UDZO "$OUT"
rm -rf "$STAGE"
echo "wrote $OUT"
