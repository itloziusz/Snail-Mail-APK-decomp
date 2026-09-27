# Building and validating the Snail Mail arm64-v8a port

Everything here builds *reconstructed source* for AArch64. Nothing in the
shipping path executes ARM32 code: no interpreter, binary translation, JIT,
QEMU or bundled 32-bit library. QEMU and Unicorn appear below only as test
vehicles (running AArch64 unit tests on an x86 host, and running original ARM32
routines for reference comparisons); they are never packaged.

## 1. Toolchain pins

| Component | Pin | Where pinned | Analysis sandbox status (2026-09-27) |
|---|---|---|---|
| Gradle | 8.14.3 | `android/gradle/wrapper/gradle-wrapper.properties` | installed at `/opt/gradle-8.14.3`; the wrapper's own download (`downloads.gradle.org`) is blocked, and so is the distribution checksum, which is left as a TODO in the properties file |
| Android Gradle Plugin | 8.13.0 (needs Gradle ≥ 8.13, JDK ≥ 17) | `android/build.gradle.kts` | **not resolvable**: every `maven.google.com` artifact redirects to `dl.google.com`, blocked by policy; the version could not be checked against the Maven metadata (attempted, see §5) |
| JDK | 17 or newer (21.0.10 here) | `compileOptions` = 17 | available |
| compileSdk / targetSdk | 35 (Android 15) | `android/app/build.gradle.kts` | not installed (only Debian's `android-sdk-platform-23`) |
| minSdk | 23 | same | 23 = first level that loads uncompressed libraries directly from the APK and rejects text relocations |
| NDK | r27c, `27.2.12479018` | `android.ndkVersion` | not available (`dl.google.com`) |
| CMake for AGP | 3.22.1 (SDK package) | `externalNativeBuild.cmake.version` | not available; host CMake 3.28 used for host/AArch64 builds |
| ABI | `arm64-v8a` only | `ndk.abiFilters`, CMake `FATAL_ERROR` guard | — |
| Host C/C++ | GCC/Clang 18 | — | available |
| AArch64 cross | `aarch64-linux-gnu-gcc` 13 + `qemu-aarch64` 8.2 | `cmake/aarch64-linux-gnu.toolchain.cmake` | available |
| ARMv7 cross (tests only) | `arm-linux-gnueabi-gcc` + `qemu-arm` | `tools/validation/abi/c/run_abi_c_checks.sh` | available |
| Python analysis deps | capstone 5.0.9, pyelftools 0.33, lief 1.0.0, unicorn 2.1.4 | `tools/bootstrap/tools.lock.json` | available |
| APK tools | `apksigner`, `zipalign`, `aapt` (Debian builds) | — | available; this `zipalign` has no `-P 16` (use build-tools ≥ 35 or the checker in §4) |

Versions that could not be verified here are marked as such in the build
files. Re-verify AGP ↔ Gradle ↔ NDK compatibility on a machine with Google
Maven access before relying on them, and fill in `distributionSha256Sum`.

## 2. Host, sanitizer and AArch64 builds of the reconstructed modules

The top-level `CMakeLists.txt` adds every `reconstructed/<area>/` with a
`CMakeLists.txt` (static library `sm_<area>`) and every
`tests/unit/<area>/` test directory.

```sh
# host (x86-64) build + tests
cmake -S . -B build-host && cmake --build build-host -j && ctest --test-dir build-host --output-on-failure

# ASan + UBSan
cmake -S . -B build-asan -DSM_SANITIZE=ON && cmake --build build-asan -j && ctest --test-dir build-asan --output-on-failure

# AArch64 (glibc) cross build; CTest runs every test through qemu-aarch64
cmake -S . -B build-a64 -DCMAKE_TOOLCHAIN_FILE=cmake/aarch64-linux-gnu.toolchain.cmake
cmake --build build-a64 -j && ctest --test-dir build-a64 --output-on-failure
```

Recorded run (2026-09-27, build dirs under `work/scratch-platform/build/`):
only `reconstructed/assets` existed (other areas empty); all three
configurations built and passed **4/4** tests (`assets.rhash`,
`assets.archive_synthetic`, `assets.archive_real`, `assets.extractor_fixture`).
The AArch64 test binaries are `ELF 64-bit … ARM aarch64` and CTest invoked
them as `qemu-aarch64 -L /usr/aarch64-linux-gnu …`.

Flags: the top-level build sets `-ffp-contract=off -fno-strict-aliasing`; the
Android native build additionally sets `-funsigned-char -fwrapv`
(`docs/ABI_PORTING.md` §6, §14). Adding those two to the top-level build is
proposed so host tests see the device semantics.

## 3. Android build

Prerequisites (outside the repo): Android SDK with `platforms;android-35`,
`build-tools;35.0.0`, `ndk;27.2.12479018`, `cmake;3.22.1`; a
`android/local.properties` with `sdk.dir=…` (gitignored).

```sh
cd android
./gradlew :app:assembleDebug                 # or: gradle :app:assembleDebug with Gradle 8.14.3
./gradlew :app:verifyNativeAlignment         # runs tools/validation/platform/check_elf_alignment.py on the debug APK
```

* Output: `android/app/build/outputs/apk/debug/app-debug.apk`,
  applicationId `com.sandlotgames.snailmail.port` (installs next to, never
  over, the original `com.sandlotgames.snailmail`).
* Game assets are proprietary and never committed. If
  `work/apk_unzip/assets` exists (created by `tools/inventory/setup_workspace.sh`)
  it is packaged automatically; otherwise pass
  `-Psnailmail.assetsDir=/path/to/assets`. `asm.mp3` and `*.ogg` are kept
  uncompressed (`androidResources.noCompress`) because Java opens them with
  `AssetManager.openFd`.
* Native code: `android/app/src/main/cpp/CMakeLists.txt` builds
  `libsnailmail.so` from `jni_bridge.cpp` plus every `reconstructed/<area>`
  module (found relative to the repo root, i.e. `../../../../../reconstructed`
  from that directory), links `GLESv1_CM log android`, and passes
  `-Wl,-z,max-page-size=16384`. It refuses any ABI other than `arm64-v8a`.
* Until the natives are reconstructed the app **fails loudly** on start
  (`JNIDatInit` throws `IllegalStateException`; see
  `docs/PLATFORM_BOUNDARIES.md` §10) — this is intended.

### Recorded build attempt (2026-09-27, analysis sandbox, not a device build)

`gradle --no-daemon assembleDebug` in `android/` with Gradle 8.14.3 / JDK 21:

```
* Where:
Build file '/home/user/Snail-Mail-APK-decomp/android/build.gradle.kts' line: 6
* What went wrong:
Plugin [id: 'com.android.application', version: '8.13.0', apply: false] was not found in any of the following sources:
- Gradle Core Plugins (plugin is not in 'org.gradle' namespace)
- Included Builds (No included builds contain this plugin)
- Plugin Repositories (could not resolve plugin artifact 'com.android.application:com.android.application.gradle.plugin:8.13.0')
  Searched in the following repositories:
    Google
    MavenRepo
    Gradle Central Plugin Repository
BUILD FAILED in 23s
```

Root cause from `--info`: `Failed to get resource: GET. [HTTP HTTP/1.1 403 Forbidden:
https://dl.google.com/dl/android/maven2/com/android/application/com.android.application.gradle.plugin/8.13.0/com.android.application.gradle.plugin-8.13.0.pom]`.
The SDK platform 35 and the NDK would have failed next for the same reason.

What *was* verified offline instead:

* Java shell compiles against `android-23/android.jar` with `javac`
  (2 deprecation warnings, `SoundPool(int,int,int)`), and
  `tools/validation/platform/check_shell_parity.py` confirms: all 18 original
  native declarations present with identical descriptors (MyOpenFeintDelegate
  intentionally removed), all 26 `gJAVAFunction` callbacks present on
  `ADRenderer` with identical descriptors, and the bridge exports exactly the
  18 original `Java_*` symbols.
* Smoke build of the Android CMake project with the *glibc* AArch64 cross
  toolchain and stub `liblog`/`libGLESv1_CM`/`libandroid` (not a substitute
  for the NDK): `libsnailmail.so` links, exports 18 `Java_*` symbols, and
  passes the 16 KB checker (ELF64 AArch64, both `PT_LOAD` `p_align 0x4000`,
  no TEXTREL, non-exec stack). Requesting `ANDROID_ABI=armeabi-v7a` stops at
  the `FATAL_ERROR` guard. Commands:

```sh
S=work/scratch-platform/android-cmake-smoke   # stub headers/libs created there
cmake -S android/app/src/main/cpp -B $S/build -DANDROID_ABI=arm64-v8a \
  -DCMAKE_TOOLCHAIN_FILE=$PWD/cmake/aarch64-linux-gnu.toolchain.cmake \
  -DCMAKE_CXX_FLAGS="-I$JAVA_HOME/include -I$JAVA_HOME/include/linux -I$PWD/$S/stubs/include" \
  -DCMAKE_SHARED_LINKER_FLAGS="-L$PWD/$S/stubs/lib"
cmake --build $S/build && python3 tools/validation/platform/check_elf_alignment.py $S/build/libsnailmail.so
```

### 3a. NDK-less development APK (what was actually built in this pass)

Because the Gradle route above is blocked here, `tools/android_build/build_dev_apk.sh`
builds the same app from the same sources with tools that are reachable:

| Step | Tool | Notes |
|---|---|---|
| native `libsnailmail.so` | host clang 18 + ld.lld, `--target=aarch64-linux-android24` | `tools/android_build/ndkless/build_so.py`: links against stub `libc.so`/`liblog.so` generated from `ndkless/symbols/*.txt` (the NDK's own stub-library technique) and the minimal headers in `ndkless/include/`; then **verifies** ELF64 AArch64, PT_LOAD ≥ 16 KiB, no TEXTREL, NEEDED ⊆ stubs, every import allowlisted |
| Java → DEX | `javac -source 8 -target 8` against Ubuntu's `android-sdk-platform-23` `android.jar`; `dalvik-exchange` (dx 10.0.0) `--min-sdk-version=23` | the shell has no lambdas, so no desugaring is needed |
| resources/assets | Debian `aapt` | `--rename-manifest-package com.sandlotgames.snailmail.port`, minSdk 23, targetSdk 35, `--debug-mode`; `asm.mp3`, `.ogg`, `resources.arsc` stored |
| alignment | `tools/android_build/zipalign16k.py` | `.so` data at 16 KiB, other stored entries at 4 B, using zipalign's `0xD935` extra field so apksigner keeps it |
| signing | Ubuntu `apksigner` 0.9 | v1+v2+v3 (v1 because minSdk 23 < 24), local debug key in `work/android_build/keys/` (never committed) |
| checks | `apksigner verify`, `check_elf_alignment.py`, `aapt dump badging`, entry-storage asserts | all run by the script |

```sh
tools/inventory/setup_workspace.sh      # needs original/com.sandlotgames.snailmail-1.00.apk
tools/android_build/build_dev_apk.sh    # -> work/android_build/out/snailmail-port-arm64-dev.apk
```

Limitations: the stub-library/minimal-header approach is a stop-gap. It
removes the NDK's safety net of complete, versioned headers. That risk is
contained by the import allowlist and `-Werror=implicit-function-declaration`,
but the build must move to the pinned NDK (§3) as soon as it is reachable.
The APK contains the original game's assets extracted from the owner-supplied
APK and must not be redistributed.

## 4. 16 KB page-size and 64-bit-only verification

`tools/validation/platform/check_elf_alignment.py FILE...` accepts `.so`
files and APKs. Rules: ELF64 + `EM_AARCH64` + `ET_DYN` (E1), every
`PT_LOAD` `p_align ≥ 16384` (E2) and `p_offset ≡ p_vaddr (mod p_align)` (E3),
no `DT_TEXTREL` (E4), non-executable `PT_GNU_STACK` (E5); for APKs every
`lib/**/*.so` must be under `lib/arm64-v8a/` (A1), stored (A2), with its data
at a 16384-aligned offset (A3), and at least one arm64 library must exist
(A4). Exit status 0 = pass.

```sh
python3 tools/validation/platform/check_elf_alignment.py --self-test
python3 tools/validation/platform/check_elf_alignment.py android/app/build/outputs/apk/debug/app-debug.apk
python3 tools/validation/platform/check_elf_alignment.py --json some.so
# with Android build-tools >= 35:
zipalign -c -P 16 -v 4 app-debug.apk
llvm-readelf -lW libsnailmail.so | grep LOAD
```

Self-test (recorded): builds `lib16k.so` (`-z max-page-size=16384`) → PASS,
`lib4k.so` (`-z max-page-size=4096`) → FAIL (E2), a stored/16 KB-aligned test
APK → PASS, a deflated APK that also contains `lib/armeabi-v7a/` → FAIL
(A1, A2, A3, E2); the original v7a library → FAIL (E1 ELF32 ARM, E2
`p_align 0x1000`, E4 `DT_TEXTREL`). Artifacts in
`work/scratch-platform/elfalign-selftest/` (gitignored). The original APK
fails A1–A4 (both libs deflated, unaligned, under 32-bit ABI dirs, no arm64
library) besides E1/E2/E4.

## 5. Signing

Debug builds use AGP's debug keystore (`~/.android/debug.keystore`, created
by AGP, outside the repo). For anything distributed, create a key outside the
repository (`*.keystore`/`*.jks` are gitignored anyway):

```sh
keytool -genkeypair -keystore ~/keys/snailmail-port.jks -alias port \
        -keyalg RSA -keysize 4096 -validity 10000
apksigner sign --ks ~/keys/snailmail-port.jks --out app-release-signed.apk app-release-unsigned.apk
apksigner verify --verbose --print-certs app-release-signed.apk
```

Sign *after* aligning (AGP does both; if signing manually, run `zipalign -P 16`
first, since signing must not move data afterwards).

## 6. Device validation — NOT RUN (no device in the analysis sandbox)

Run on a 64-bit-only device or an Android 15+ 16 KB-page emulator image:

```sh
adb shell getprop ro.product.cpu.abilist      # must contain arm64-v8a
adb shell getprop ro.product.cpu.abilist32    # empty on a 64-bit-only device: no 32-bit fallback possible
adb shell getconf PAGE_SIZE                   # 16384 on a 16 KB-page device/emulator
adb install -r android/app/build/outputs/apk/debug/app-debug.apk
adb shell pm dump com.sandlotgames.snailmail.port | grep -E "primaryCpuAbi|secondaryCpuAbi"   # arm64-v8a / null
adb shell am start -n com.sandlotgames.snailmail.port/com.sandlotgames.snailmail.SnailMailActivity
PID=$(adb shell pidof com.sandlotgames.snailmail.port)
adb shell cat /proc/$PID/maps | grep libsnailmail      # mapped from base.apk!/lib/arm64-v8a/libsnailmail.so
adb shell readlink /proc/$PID/exe                       # /system/bin/app_process64
adb logcat -s SnailMailPort:* AndroidRuntime:*          # currently: IllegalStateException from JNIDatInit (expected)
adb shell run-as com.sandlotgames.snailmail.port ls -l files   # asm.cfg, of.cfg, RandTable.bin once natives exist
```

Record device model, build fingerprint (`getprop ro.build.fingerprint`) and
the outputs above in the evidence ledger before claiming
`ARM64_DEVICE_VALIDATED` for anything.

## 7. Analysis tools of the platform/validation area

| Command | Output |
|---|---|
| `python3 tools/validation/platform/import_census.py` | `analysis/native/platform_imports.json` |
| `python3 tools/validation/platform/gl_census.py` | `analysis/native/gl_usage.json` |
| `python3 tools/validation/platform/callsite_args.py NAME…` | recovered arguments at call sites |
| `python3 tools/validation/platform/check_shell_parity.py` | JNI contract parity of `android/` vs original |
| `python3 tools/validation/platform/check_elf_alignment.py` | 16 KB / arm64-only checks |
| `python3 tools/validation/abi/abi_audit.py [--json F]` | instruction/ABI census |
| `python3 tools/validation/abi/unicorn_runtime_probe.py` | original-code semantics probes (Unicorn) |
| `python3 tools/validation/abi/trig_table_sensitivity.py` | libm sensitivity of `RMathSin/Cos` |
| `tools/validation/abi/c/run_abi_c_checks.sh` | rand48 / float→int / FP-contraction on host, AArch64, ARMv7 |

All read `work/apk_unzip/…` (populate with `tools/inventory/setup_workspace.sh`)
and never modify inputs.
