#!/usr/bin/env bash
# Build the arm64-v8a-only DEVELOPMENT APK of the Snail Mail port WITHOUT the
# Android Gradle Plugin, SDK build-tools or NDK (all served from dl.google.com,
# which is blocked on the analysis machine). When those are available, use the
# Gradle project in android/ instead (docs/BUILDING.md).
#
# Inputs:  android/ (Java shell, manifest, res, jni_bridge.cpp), reconstructed/,
#          original assets from work/apk_unzip/assets (tools/inventory/setup_workspace.sh)
# Output:  work/android_build/out/snailmail-port-arm64-dev.apk    (aot, bridge)
#          work/android_build/out/snailmail-port-arm64-gles2.apk  (aot-gles2)
#          (gitignored; a build replaces only its own APK)
#
# Variants (SM_APK_VARIANT):
#   aot (default): the original managed shell (minus OpenFeint) + an AArch64
#     libsnailmail.so containing the ahead-of-time translation of ALL original
#     game code (tools/aot/arm2c.py) and the AOT runtime - the playable port.
#     Renders through the device's GLES 1.1 (libGLESv1_CM, ES 1 context).
#   aot-gles2: the same game, but the GLSurfaceView requests a GLES 2 context
#     (setEGLContextClientVersion(2), patched into a temporary copy of the Java
#     sources) and libsnailmail.so renders through the GLES1-on-GLES2
#     emulation of reconstructed/rendering (smgl) on libGLESv2 instead of
#     libGLESv1_CM. Same applicationId and debug key, so the two replace each
#     other; versionCode defaults to 3 (aot: 2).
#   bridge: only the hand-reconstructed modules + fail-loudly JNI bridge.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
cd "$ROOT"
ANDROID_JAR="${ANDROID_JAR:-/usr/lib/android-sdk/platforms/android-23/android.jar}"
OUT="$ROOT/work/android_build"
KS="$OUT/keys/debug.keystore"   # local debug key, never committed
MIN_SDK=23
TARGET_SDK=35
APP_ID="${SM_APP_ID:-com.sandlotgames.snailmail.port}"
if [[ ! "$APP_ID" =~ ^[a-zA-Z_][a-zA-Z_0-9]*(\.[a-zA-Z_][a-zA-Z_0-9]*)+$ ]]; then
  echo "invalid SM_APP_ID=$APP_ID"
  exit 1
fi
VARIANT="${SM_APK_VARIANT:-aot}"
case "$VARIANT" in
  aot|bridge) APK="$OUT/out/snailmail-port-arm64-dev.apk"; VC="${SM_VERSION_CODE:-2}"; VNAME="1.00-port-dev$VC" ;;
  aot-gles2) APK="$OUT/out/snailmail-port-arm64-gles2.apk"; VC="${SM_VERSION_CODE:-3}"; VNAME="1.00-port-gles2-dev$VC" ;;
  *) echo "unknown SM_APK_VARIANT=$VARIANT (expected aot, aot-gles2 or bridge)"; exit 1 ;;
esac

for t in clang ld.lld javac dalvik-exchange aapt apksigner keytool python3; do
  command -v "$t" >/dev/null || { echo "missing tool: $t"; exit 1; }
done
[ -f "$ANDROID_JAR" ] || { echo "missing $ANDROID_JAR (apt install android-sdk-platform-23)"; exit 1; }
[ -f work/apk_unzip/assets/asm.mp3 ] || { echo "run tools/inventory/setup_workspace.sh first"; exit 1; }
for density in ldpi mdpi hdpi; do
  [ -f "work/apk_unzip/res/drawable-$density/icon.png" ] || {
    echo "missing original drawable-$density/icon.png (run tools/inventory/setup_workspace.sh)"
    exit 1
  }
done

rm -rf "$OUT/app"   # objects are cached in $OUT/obj-*
rm -f "$APK" "$APK.idsig"   # the other variant's APK is kept
mkdir -p "$OUT/app/classes" "$OUT/app/pkg/lib/arm64-v8a" "$OUT/out" "$OUT/keys"
for density in ldpi mdpi hdpi; do
  mkdir -p "$OUT/app/res/drawable-$density"
  cp "work/apk_unzip/res/drawable-$density/icon.png" "$OUT/app/res/drawable-$density/icon.png"
done

if [ "$VARIANT" = "aot" ] || [ "$VARIANT" = "aot-gles2" ]; then
  echo "== translate original ARM32 code (tools/aot/arm2c.py)"
  ORIG_SO=work/apk_unzip/lib/armeabi-v7a/libsnailmail.so
  if [ ! -f aot/generated/aot_table.c ] || ! grep -q "$(sha256sum "$ORIG_SO" | cut -d' ' -f1)" aot/generated/aot_table.c \
     || [ tools/aot/arm2c.py -nt aot/generated/aot_table.c ] || [ tools/aot/hooks.txt -nt aot/generated/aot_table.c ]; then
    python3 tools/aot/arm2c.py --elf "$ORIG_SO" --out aot/generated
  fi
  AOT_SRCS=(aot/runtime/aot_core.c aot/runtime/aot_libc.c aot/runtime/aot_gl.c aot/runtime/aot_jni.c
    aot/runtime/aot_android.c aot/generated/aot_table.c aot/generated/aot_funcs_*.c
    aot/port/port_display.c aot/port/port_menu.c aot/port/port_android.c)
  if [ "$VARIANT" = "aot" ]; then
    echo "== native (arm64-v8a, NDK-less): AOT runtime + translated game (GLES 1.1)"
    GL_ARGS=(--lib libGLESv1_CM)
    GL_LIB=libGLESv1_CM.so NOT_GL_LIB=libGLESv2.so
  else
    # Only aot_android.c (backend switch) and smgl get extra flags, so the
    # translated objects cached in obj-aot are shared with the aot variant.
    echo "== native (arm64-v8a, NDK-less): AOT runtime + translated game + GLES1-on-GLES2 emulation"
    GL_ARGS=(--lib libGLESv2
      --file-flag aot/runtime/aot_android.c=-DSM_GL_EMULATE_GLES1
      --file-flag aot/runtime/aot_android.c=-Ireconstructed/rendering/include
      --file-flag reconstructed/rendering/src=-Ireconstructed/rendering/include)
    AOT_SRCS+=(reconstructed/rendering/src/smgl.c reconstructed/rendering/src/smgl_math.c)
    GL_LIB=libGLESv2.so NOT_GL_LIB=libGLESv1_CM.so
  fi
  python3 tools/android_build/ndkless/build_so.py \
    --out "$OUT/app/pkg/lib/arm64-v8a/libsnailmail.so" --workdir "$OUT/obj-aot" \
    --lib libc --lib libm --lib liblog "${GL_ARGS[@]}" \
    -I aot/runtime -I aot/generated -I aot/port -D SM_NDKLESS_GLES_DECLS \
    --nowarn-prefix aot/generated \
    "${AOT_SRCS[@]}"
  python3 - "$OUT/app/pkg/lib/arm64-v8a/libsnailmail.so" "$GL_LIB" "$NOT_GL_LIB" <<'PY'
import sys
from elftools.elf.dynamic import DynamicSection
from elftools.elf.elffile import ELFFile
so, want, never = sys.argv[1:]
with open(so, "rb") as f:
    needed = [t.needed for s in ELFFile(f).iter_sections() if isinstance(s, DynamicSection)
              for t in s.iter_tags() if t.entry.d_tag == "DT_NEEDED"]
assert want in needed and never not in needed, needed
print(f"GL library: {want} (not {never})")
PY
elif [ "$VARIANT" = "bridge" ]; then
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
JAVA_DIR=android/app/src/main/java/com/sandlotgames/snailmail
if [ "$VARIANT" = "aot-gles2" ]; then
  # The committed sources keep GLSurfaceView's default ES 1 context; this
  # variant compiles a patched copy that requests ES 2 before setRenderer().
  mkdir -p "$OUT/app/java"
  cp "$JAVA_DIR"/*.java "$OUT/app/java/"
  python3 - "$OUT/app/java/ADGLSurfaceView.java" <<'PY'
import pathlib, re, sys
p = pathlib.Path(sys.argv[1])
s = p.read_text()
code = [l for l in s.splitlines() if not re.match(r"\s*(//|/?\*)", l)]
assert not any("setEGLContextClientVersion" in l for l in code), "already sets a client version"
anchor = "        setRenderer(this.mRenderer);\n"
assert s.count(anchor) == 1, "ADGLSurfaceView: setRenderer(this.mRenderer) not found exactly once"
s = s.replace(anchor, "        // aot-gles2 variant (build_dev_apk.sh): GLES 2 context for the\n"
                      "        // GLES1-on-GLES2 emulation in libsnailmail.so.\n"
                      "        setEGLContextClientVersion(2);\n" + anchor)
p.write_text(s)
PY
  JAVA_SRCS=("$OUT/app/java"/*.java)
else
  JAVA_SRCS=("$JAVA_DIR"/*.java)
fi
javac -nowarn -source 8 -target 8 -bootclasspath "$ANDROID_JAR" -Xlint:-options \
  -d "$OUT/app/classes" "${JAVA_SRCS[@]}"
if [ "$VARIANT" = "aot-gles2" ]; then
  grep -q setEGLContextClientVersion "$OUT/app/classes/com/sandlotgames/snailmail/ADGLSurfaceView.class" ||
    { echo "ADGLSurfaceView.class does not call setEGLContextClientVersion"; exit 1; }
fi
dalvik-exchange --dex --min-sdk-version="$MIN_SDK" --output="$OUT/app/pkg/classes.dex" "$OUT/app/classes"

echo "== resources + assets (asm.mp3 and .ogg stored uncompressed: openFd requires it)"
# The Gradle manifest takes its package from the namespace; aapt needs it inline.
sed "s|<manifest xmlns:android=\"http://schemas.android.com/apk/res/android\"|<manifest xmlns:android=\"http://schemas.android.com/apk/res/android\" package=\"com.sandlotgames.snailmail\" android:versionCode=\"$VC\" android:versionName=\"$VNAME\"|" \
  android/app/src/main/AndroidManifest.xml > "$OUT/app/AndroidManifest.xml"
if [ "$VARIANT" = "aot-gles2" ]; then
  # This variant needs OpenGL ES 2.0 (the original declares 1.1).
  sed -i 's|android:glEsVersion="0x00010001"|android:glEsVersion="0x00020000"|' "$OUT/app/AndroidManifest.xml"
  grep -q 'android:glEsVersion="0x00020000"' "$OUT/app/AndroidManifest.xml" ||
    { echo "manifest: glEsVersion not patched"; exit 1; }
fi
aapt package -f --debug-mode \
  --min-sdk-version "$MIN_SDK" --target-sdk-version "$TARGET_SDK" \
  --rename-manifest-package "$APP_ID" \
  -M "$OUT/app/AndroidManifest.xml" -S "$OUT/app/res" -S android/app/src/main/res -A work/apk_unzip/assets \
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
aapt dump badging "$APK" | grep -E "^package|sdkVersion|targetSdkVersion|uses-gl-es|native-code|launchable"
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
