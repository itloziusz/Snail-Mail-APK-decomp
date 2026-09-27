#!/usr/bin/env bash
# Recreate the local, gitignored working copies under work/ from the immutable
# original APK. Never writes into original/.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
APK="$ROOT/original/com.sandlotgames.snailmail-1.00.apk"
EXPECT=0d10908d50f2a8361d9bbd3c6c9bff025434fdfb49c0f2b97254a793fb0b29e7
TOOLS="${RE_TOOLS:-/opt/re-tools}"
[ -f "$APK" ] || { echo "missing $APK (see original/README.md)"; exit 1; }
GOT=$(sha256sum "$APK" | cut -d' ' -f1)
[ "$GOT" = "$EXPECT" ] || { echo "APK hash mismatch: $GOT"; exit 1; }
mkdir -p "$ROOT/work"
cd "$ROOT/work"
if [ ! -d apk_unzip ]; then
  mkdir apk_unzip && (cd apk_unzip && unzip -q "$APK") && chmod -R a-w apk_unzip
fi
[ -d apktool ] || java -jar "$TOOLS/apktool_2.12.1.jar" d -f -o apktool "$APK"
[ -d jadx ] || "$TOOLS/jadx-1.5.3/bin/jadx" -d jadx --show-bad-code "$APK" || true
echo "workspace ready: $ROOT/work"
