#!/usr/bin/env bash
# Build the arm64-v8a-only DEVELOPMENT APK of the Snail Mail port WITHOUT the
# Android Gradle Plugin, SDK build-tools or NDK (all served from dl.google.com,
# which is blocked on the analysis machine). When those are available, use the
# Gradle project in android/ instead (docs/BUILDING.md).
#
# Inputs:  android/ (Java shell, manifest, res, jni_bridge.cpp), reconstructed/,
#          original assets from work/apk_unzip/assets (tools/inventory/setup_workspace.sh)
# Output:  work/android_build/out/snailmail-port-arm64-dev.apk  (gitignored)
#
# Variants (SM_APK_VARIANT):
#   aot (default): the original managed shell (minus OpenFeint) + an AArch64
#     libsnailmail.so containing the ahead-of-time translation of ALL original
#     game code (tools/aot/arm2c.py) and the AOT runtime - the playable port.
#   bridge: only the hand-reconstructed modules + fail-loudly JNI bridge.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
cd "$ROOT"
ANDROID_JAR="${ANDROID_JAR:-/usr/lib/android-sdk/platforms/android-23/android.jar}"
OUT="$ROOT/work/android_build"
KS="$OUT/keys/debug.keystore"   # local debug key, never committed
MIN_SDK=23
TARGET_SDK=35
APP_ID=com.sandlotgames.snailmail.port
APK="$OUT/out/snailmail-port-arm64-dev.apk"

for t in clang ld.lld javac dalvik-exchange aapt apksigner keytool python3; do
  command -v "$t" >/dev/null || { echo "missing tool: $t"; exit 1; }
done
[ -f "$ANDROID_JAR" ] || { echo "missing $ANDROID_JAR (apt install android-sdk-platform-23)"; exit 1; }
[ -f work/apk_unzip/assets/asm.mp3 ] || { echo "run tools/inventory/setup_workspace.sh first"; exit 1; }

rm -rf "$OUT/app" "$OUT/out"   # objects are cached in $OUT/obj-*
mkdir -p "$OUT/app/classes" "$OUT/app/pkg/lib/arm64-v8a" "$OUT/out" "$OUT/keys"

VARIANT="${SM_APK_VARIANT:-aot}"
if [ "$VARIANT" = "aot" ]; then
  echo "== translate original ARM32 code (tools/aot/arm2c.py)"
  ORIG_SO=work/apk_unzip/lib/armeabi-v7a/libsnailmail.so
  if [ ! -f aot/generated/aot_table.c ] || ! grep -q "$(sha256sum "$ORIG_SO" | cut -d' ' -f1)" aot/generated/aot_table.c; then
    python3 tools/aot/arm2c.py --elf "$ORIG_SO" --out aot/generated
  fi
  echo "== native (arm64-v8a, NDK-less): AOT runtime + translated game"
  python3 tools/android_build/ndkless/build_so.py \
    --out "$OUT/app/pkg/lib/arm64-v8a/libsnailmail.so" --workdir "$OUT/obj-aot" \
    --lib libc --lib libm --lib liblog --lib libGLESv1_CM \
    -I aot/runtime -I aot/generated -D SM_NDKLESS_GLES_DECLS \
    --nowarn-prefix aot/generated \
    aot/runtime/aot_core.c aot/runtime/aot_libc.c aot/runtime/aot_gl.c aot/runtime/aot_jni.c \
    aot/runtime/aot_android.c aot/generated/aot_table.c aot/generated/aot_funcs_*.c
else
  echo "== native (arm64-v8a, NDK-less): reconstructed modules + JNI bridge (fails at nativeInit)"
  python3 tools/android_build/ndkless/build_so.py \
    --out "$OUT/app/pkg/lib/arm64-v8a/libsnailmail.so" --workdir "$OUT/app/obj" \
    --lib libc --lib liblog \
    -I reconstructed/assets/include -I reconstructed/platform/include \
    android/app/src/main/cpp/jni_bridge.cpp \
    reconstructed/assets/src/rhash.c reconstructed/assets/src/asm_archive.c \
    reconstructed/platform/src/dat.c
fi

echo "== java -> dex"
javac -nowarn -source 8 -target 8 -bootclasspath "$ANDROID_JAR" -Xlint:-options \
  -d "$OUT/app/classes" android/app/src/main/java/com/sandlotgames/snailmail/*.java
dalvik-exchange --dex --min-sdk-version="$MIN_SDK" --output="$OUT/app/pkg/classes.dex" "$OUT/app/classes"

echo "== resources + assets (asm.mp3 and .ogg stored uncompressed: openFd requires it)"
# The Gradle manifest takes its package from the namespace; aapt needs it inline.
VC="${SM_VERSION_CODE:-2}"
sed "s|<manifest xmlns:android=\"http://schemas.android.com/apk/res/android\"|<manifest xmlns:android=\"http://schemas.android.com/apk/res/android\" package=\"com.sandlotgames.snailmail\" android:versionCode=\"$VC\" android:versionName=\"1.00-port-dev$VC\"|" \
  android/app/src/main/AndroidManifest.xml > "$OUT/app/AndroidManifest.xml"
aapt package -f --debug-mode \
  --min-sdk-version "$MIN_SDK" --target-sdk-version "$TARGET_SDK" \
  --rename-manifest-package "$APP_ID" \
  -M "$OUT/app/AndroidManifest.xml" -S android/app/src/main/res -A work/apk_unzip/assets \
  -I "$ANDROID_JAR" -0 arsc -0 mp3 -0 ogg -F "$OUT/app/unaligned.apk"

python3 - "$OUT/app/unaligned.apk" "$OUT/app/pkg" <<'PY'
import sys, zipfile
apk, pkg = sys.argv[1], sys.argv[2]
with zipfile.ZipFile(apk, "a") as z:
    z.write(f"{pkg}/classes.dex", "classes.dex", compress_type=zipfile.ZIP_DEFLATED)
    # Stored + page-aligned so the loader can map it in place (extractNativeLibs=false).
    z.write(f"{pkg}/lib/arm64-v8a/libsnailmail.so", "lib/arm64-v8a/libsnailmail.so",
            compress_type=zipfile.ZIP_STORED)
PY

echo "== align (16 KiB for .so) + sign (v1+v2+v3, local debug key)"
python3 tools/android_build/zipalign16k.py "$OUT/app/unaligned.apk" "$OUT/app/aligned.apk"
if [ ! -f "$KS" ]; then
  keytool -genkeypair -keystore "$KS" -storepass android -keypass android -alias androiddebugkey \
    -keyalg RSA -keysize 2048 -validity 10000 -dname "CN=Snail Mail Port Debug,O=Local Development"
fi
# minSdk 23 predates APK Signature Scheme v2 (API 24), so v1 is required too.
apksigner sign --ks "$KS" --ks-pass pass:android --v1-signing-enabled true \
  --v2-signing-enabled true --v3-signing-enabled true --out "$APK" "$OUT/app/aligned.apk"

echo "== verify"
apksigner verify --verbose "$APK" | grep -E "Verifies|v1 scheme|v2 scheme|v3 scheme"
python3 tools/validation/platform/check_elf_alignment.py "$APK"
aapt dump badging "$APK" | grep -E "^package|sdkVersion|targetSdkVersion|native-code|launchable"
python3 - "$APK" <<'PY'
import sys, zipfile
z = zipfile.ZipFile(sys.argv[1])
names = z.namelist()
libs = [n for n in names if n.startswith("lib/")]
assert libs == ["lib/arm64-v8a/libsnailmail.so"], libs
assert z.getinfo("assets/asm.mp3").compress_type == zipfile.ZIP_STORED
assert z.getinfo("resources.arsc").compress_type == zipfile.ZIP_STORED
print(f"entries={len(names)} libs={libs} asm.mp3=stored resources.arsc=stored")
PY
sha256sum "$APK"
