#!/usr/bin/env bash
# Builds dist/WaferGuard (folder) and dist/WaferGuard-1.0.0-x86_64.AppImage.
# Run on the OLDEST Linux you want to support (glibc compatibility), e.g. Ubuntu 20.04.
set -euo pipefail
cd "$(dirname "$0")/.."
python3 -m pip install -r requirements-desktop.txt
( cd frontend && npm install --no-audit --no-fund && npx vite build )
test -f models/wafer-ensemble/manifest.json || { echo "train or copy a model into models/wafer-ensemble first"; exit 1; }
pyinstaller packaging/waferguard.spec --noconfirm --distpath dist --workpath build

APPDIR=build/WaferGuard.AppDir
rm -rf "$APPDIR" && mkdir -p "$APPDIR/usr/bin"
cp -r dist/WaferGuard "$APPDIR/usr/bin/"
cp packaging/AppRun packaging/waferguard.desktop "$APPDIR/"
if command -v rsvg-convert >/dev/null; then rsvg-convert -w 256 -h 256 packaging/waferguard.svg > "$APPDIR/waferguard.png"
else cp packaging/waferguard.svg "$APPDIR/waferguard.svg"; fi
if ! command -v appimagetool >/dev/null; then
  curl -L -o build/appimagetool https://github.com/AppImage/appimagetool/releases/download/continuous/appimagetool-x86_64.AppImage
  chmod +x build/appimagetool; TOOL=build/appimagetool
else TOOL=appimagetool; fi
ARCH=x86_64 "$TOOL" "$APPDIR" dist/WaferGuard-1.0.0-x86_64.AppImage
echo "Built dist/WaferGuard-1.0.0-x86_64.AppImage"
