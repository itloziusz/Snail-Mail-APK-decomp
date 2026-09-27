# Porting status

## Preview build — 2026-09-27

The `aot-gles2` branch at `6de0c2b` was built from the reference APK whose
SHA-256 is `0d10908d50f2a8361d9bbd3c6c9bff025434fdfb49c0f2b97254a793fb0b29e7`.
The corrected versionCode 1 APK has SHA-256
`333679b4ef8ad9079761e4dc0e230ff404f5fbf7a8d1c232a68b630c3b6bdd6f`.
`apksigner verify` passed for v1, v2, and v3; the ELF/APK checker passed
arm64-v8a only, stored native library, 16 KiB alignment, and no text
relocations. `aapt` reports package `com.sandlotgames.snailmail.port.preview`,
minSdk 23, targetSdk 35, and GLES 2.0. This build uses a local debug key.
The unique preview package ID avoids signature conflicts with older test
installs of `com.sandlotgames.snailmail.port`; those installs retain their data.

This verifies a build artifact, not a device run. The GLES2 renderer, 60 Hz
pacer, adaptive FOV, HUD alignment, and Display page in this revision still
need hands-on device testing. The first downloadable APK is therefore marked
as an experimental preview; see `RELEASE_NOTES_v0.1.0.md`.

Local verification of this revision: eight available host checks passed with
ASan/UBSan (`LSAN_OPTIONS=detect_leaks=0`, because LeakSanitizer cannot inspect
processes in this sandbox); the optional shell-parity check was skipped because
apktool output was absent. The Clang host runner booted 6,000 frames without a
fatal diagnostic at 800×480 and at 2400×1080 with adaptive settings. Mesa
GLES2 offscreen renders at 1600×900 reached the main menu, then touch input
opened Options and the new Display page. These are host checks, not Android
device validation or a new differential trace against the original binary.

## Pass 2 — 2026-09-27: the game runs natively

**The complete game logic runs as natively compiled code**, through the
ahead-of-time translation described in `docs/ARCHITECTURE.md` Revision 2.
It is validated on a Linux host and on AArch64 under qemu-user. The owner
reports that it runs on a Galaxy S24+ (Exynos 2400, Xclipse GPU); see "Device
reports" below. No device logs or captures from that run are in this repository.

| Check | Result |
|---|---|
| Translation | 1,173/1,173 functions of v7a `e43bc913…a466`. 44 unsupported sites, all in the libgcc exception unwinder, which the game never reaches. |
| Host run: x86-64, GLES1-on-GLES2 offscreen | Sandlot and Alpha72 splashes, loading screen, intro crawl, main menu, mode menu, Tutorial level: steering, packages, asteroids, turrets, sound and music requests. No fatal diagnostics. |
| Differential vs the original ARM32 code (Unicorn 2) | 6,000 frames: **identical GL traces** (2,213,927 calls incl. data hashes) and identical save files. Unicorn executed 196 M original instructions in the first 1,600 frames. |
| AArch64 execution (GCC 13 cross-build, qemu-aarch64 test harness) | Same 6,000 frames: GL trace and save files identical to the original. |
| Android APK (`tools/android_build/build_dev_apk.sh`, AOT variant) | Built. `libsnailmail.so` is ELF64 AArch64 with 16 KiB alignment, 3.1 MB and 91 allowlisted imports. The APK signature verifies with v1, v2 and v3. |
| Device run | **Owner report only:** Galaxy S24+ (Exynos 2400 / Xclipse), versionCode 2: "The game runs", but faster than normal. Not observed by this project; see below. |
| Cost of game logic (headless, this Xeon) | ≈0.12 ms CPU per frame; 27 MiB peak RSS. |

### Device reports (Galaxy S24+, Exynos 2400 / Xclipse GPU)

These are the owner's reports. The project has no device logs, screenshots or
frame captures from them.

1. **versionCode 1: "Failed to launch".** Root cause (from the code, not a
   device log): the shell targets SDK 35 and keeps the original
   `registerListener(..., SENSOR_DELAY_FASTEST)`. On Android 12+, a debuggable
   app that asks for more than 200 Hz without `HIGH_SAMPLING_RATE_SENSORS`
   gets a `SecurityException` from `SystemSensorManager.enableSensor`, inside
   `onCreate`. **Fix (versionCode 2):** declare the permission.
2. **versionCode 2: "The game runs … Looks like it runs faster."** Root cause:
   `appRender` (v7a:0x15690, `docs/BOOT_CHAIN.md` 3.2) runs **at least one**
   16,666 µs update for every frame the GLSurfaceView draws. The S24+ panel
   refreshes at up to 120 Hz, so the game ran about twice as fast.
   * Reproduced on the host with the new `--hz` option (virtual display rate).
   * An offline simulation of the `appRender` update table gives the same
     result: 120.1 updates/s at 120 Hz against 60.2 at 60 Hz.

   **Fix (versionCode 4, not yet confirmed on the device).** The shell
   restores the 60 Hz presentation the game was written for, in three layers:
   * `SnailMailActivity` requests the display's 60 Hz mode;
   * the surface is declared as fixed-rate 60 fps content
     (`Surface.setFrameRate`, Android 11+);
   * a Choreographer pacer draws on vsync, but never twice within
     14.67 ms (one 60 Hz period minus 2 ms).

   The simulation with the pacer gives 60.2 updates/s at 120 Hz and 60.1 at
   90 and 144 Hz, where the game's own catch-up steps cover the slower frame
   rate. At 60 Hz every vsync is still drawn. The game's timing code and its
   clock (`System.nanoTime`) are unchanged, and no speed multiplier is used.

Known risks on a device:

* **Toolchain.** The APK was built without the NDK, using stub libraries and
  hand-written headers (`tools/android_build/ndkless`).
* **Real GLES1 driver.** Behaviour on real GLES1 drivers is unverified.
* **4 GiB reservation.** The virtual reservation must succeed.
* **`FileDescriptor` access.** It goes through `ParcelFileDescriptor.dup`,
  which leaks one fd per `onCreate`.
* **Audio after `onStop`/`onRestart`.** It may be silent (original behaviour,
  hypothesis).
* **Default random seed.** `rand48` uses bionic's default seed (hypothesis).

---

## Pass 1

Pass 1 — 2026-09-27. Reference binary `v7a` = `lib/armeabi-v7a/libsnailmail.so`,
SHA-256 `e43bc913e9ba99abd2fed4d2cee40d4a33a951ecbcf8ca154d099cc8cabaa466`, from
APK SHA-256 `0d10908d50f2a8361d9bbd3c6c9bff025434fdfb49c0f2b97254a793fb0b29e7`.

**Bottom line: the game does not run on ARM64 yet.** The project has a verified
analysis foundation, a documented startup path, and the first reconstructed
modules. Those modules are reference-validated against the original machine
code and packaged in a genuine arm64-v8a APK. That APK stops, by design, at the
first unreconstructed entry point (`nativeInit`).

## 1. What was inspected

| Input | SHA-256 | Result |
|---|---|---|
| APK `com.sandlotgames.snailmail` 1.00 (versionCode 1) | `0d10908d…29e7` | v1 signature verifies (Sandlot Games cert `5b3cca3a…ac3b`); inventory in `analysis/apk/inventory.json` |
| `lib/armeabi-v7a/libsnailmail.so` | `e43bc913…a466` | ELF32 ARM, EABI5, ARMv7 VFPv2 softfp, pure ARM mode, unstripped, GCC 4.4.0 |
| `lib/armeabi/libsnailmail.so` | `96dbeaeb…8136` | ELF32 ARM v5TE soft-float; same 1,096 game functions by name plus 70 soft-float helpers |
| `classes.dex` | `b430e061…7459` | 353 classes: 18 game shell, 252 OpenFeint, 67 Jackson, 10 commons-codec, 6 google escape |
| `assets/asm.mp3` | `59740ec3…1b6a`* | custom archive: 734 records, fully decoded from consuming code |

\* full hash `59740ec3a2cd1f7e9ff250e3c6128ffff922193925e3d0316db22a4118861b6a`.

Earlier-project leads were re-verified in `docs/HISTORICAL_LEADS.md`. No earlier
code, traces, Plume or LegacyRenderIR artifacts exist in this environment.

## 2. Coverage (v7a)

Denominator: **1,114 non-runtime functions** = 1,096 game/engine + 18 JNI
(`analysis/native/function_summary.v7a.json`). The 59 libgcc/EABI runtime
helpers are excluded, because the AArch64 toolchain provides its own.

| Milestone | Count | % of 1,114 |
|---|---|---|
| DISCOVERED | 1,114 | 100 % |
| DISASSEMBLED | 1,114 | 100 % |
| DECOMPILED (Ghidra 11.4.2 output only; not a correctness claim) | 1,114 | 100 % |
| RECONSTRUCTED (full semantics; 7 non-trivial + 7 empty/trivial bodies) | 14 | 1.3 % |
| RECONSTRUCTED (partial: helper semantics only) | 5 | 0.4 % |
| UNIT_TESTED | 8 | 0.7 % |
| REFERENCE_VALIDATED (differential vs original instructions) | 6 | 0.5 % |
| ARM64_DEVICE_VALIDATED | 0 | 0 % |

The reconstructed functions:

| v7a address | Original function | Reconstruction | Status |
|---|---|---|---|
| 0x7d114 | `cRHash::Calc` | `sm_rhash_calc` | REFERENCE_VALIDATED |
| 0x7d144 | `cRHash::Add` | `sm_rhash_add` | REFERENCE_VALIDATED |
| 0x7d190 | `cRHash::Search` | `sm_rhash_search` | REFERENCE_VALIDATED |
| 0x7d340 | `cRHash::Init` | `sm_rhash_init` | REFERENCE_VALIDATED |
| 0x7d338 | `cRHash::UnInit` | `sm_rhash_uninit` | UNIT_TESTED |
| 0x19954 | `DatHashGetString` | directory name getter in `sm_asm_directory` | REFERENCE_VALIDATED (via the JNIDatInit suite) |
| 0x15244 | `JNIDatInit` | `sm_dat_open` + bridge | REFERENCE_VALIDATED (directory and index); fd acquisition is a documented port change |
| 0x13ad0, 0x13b44, 0x13c70, 0x1555c | `JNIAudioInit`, `JNIDebug`, `JNIDatUnInit`, `nativeDone` | trivial bridge bodies (`bx lr` / `return 0` / empty callees) | UNIT_TESTED (JNIDebug) / established by inspection |
| 0x1568c, 0x15498, 0x19948 | `appDeinit`, `importGLDeinit`, `wprintf` | empty in the original; no-ops | established by inspection |
| 0x14c74, 0x1b980, 0x144c0, 0x149e8, 0x14b60 | `PfmLoadFileDat`, `RShellLoadFile`, `JAVAC_UnPng`, `JAVAC_UnJpg`, `JAVAC_UnZip` | read spans, output extents, TGA header, row placement (`sm_asm_*`) | partial; those aspects REFERENCE_VALIDATED |

## 3. Recovered execution path

Full detail is in `docs/BOOT_CHAIN.md`.

1. **Load.** `SnailMailActivity.<clinit>` calls `System.loadLibrary("snailmail")`. There is no `JNI_OnLoad`, and 11 `.init_array` constructors run.
2. **`onCreate` (main thread).** It calls `JNIDatInit(fd, start, len)`, which opens `asm.mp3` and builds the `gDatHash` index. **Reconstructed.**
3. **GL thread, first surface.** `nativeInit` runs `JAVA_RegisterFunctions` (caching the JNIEnv, `thiz` and 26 method IDs as raw locals), then `cRResourceManager::Init`. **Unreconstructed — current boundary.**
4. **Every frame.** `nativeRender(pause)` calls `AppInit()`, which is a per-frame state machine over `gAppState` 0..10:
   - state 0: `RShellInit`, `RMathInit`, `new cRGame` (0x3a6468 bytes), `InitGL`
   - state 2: load `asm.cfg`
   - states 1 and 3–7: `cRGame::Init0`..`Init5`
   - state 8: textures
   - state 9: `InitLast`

   It then calls `appRender`, which runs fixed 16,666 µs steps (1–4 per frame): `FontAI` → `cRGame::AI`, then `G0Render`.
5. **Clock.** `System.nanoTime()/1000` via the `JAVATime`/`JAVATimeHi` callbacks.

The first unresolved boundaries are listed in `docs/BOOT_CHAIN.md` §6:
- vtable `AI()` dispatch in `cRGame::AI` (0x3b12c, 0x3b17c, 0x3b194) and 10 similar sites;
- the `cRGame` field layout;
- the `gConfig` semantics;
- the `.SMO` model format (364 archive records).

## 4. Implemented

- **`reconstructed/assets` (`sm_assets`, C11).** Archive directory parser, `cRHash`, read spans, TGA header and row placement. On-disk fields stay `uint32_t`. The original's in-place 32-bit pointer fix-up is replaced by offsets.
- **`reconstructed/platform` (`sm_platform`, C11).** The native side of `JNIDatInit`. It holds an owned dup'd fd and reads with positioned reads; every size is validated.
- **`android/`.** Gradle project (AGP 8.13.0, SDK 35, NDK 27.2 pins; build blocked here), Java shell with the original class and JNI names, and `jni_bridge.cpp`, which exports all 18 original symbols. 5 are implemented; 13 throw `IllegalStateException`.
- **`tools/android_build/`.** An NDK-less arm64 build path and a development APK builder (`docs/BUILDING.md` §3a).
- **Analysis tooling.** Inventory, ELF audit, function index, Ghidra headless export, class, import and GL censuses, ABI audit, JNI scans, the archive extractor, and the Unicorn ARM32 reference harness (analysis-only).

### Development APK

`work/android_build/out/snailmail-port-arm64-dev.apk` (gitignored; it contains the owner's original assets):
- **Identity:** SHA-256 `ec82f9aca905a954e36903921fd3459119d0688d8b9f7365138623ecb5ffc435`, 11,337,392 B, built from commit `94adb70`.
- **Package:** `com.sandlotgames.snailmail.port`, minSdk 23, targetSdk 35, debuggable.
- **Native code:** only `lib/arm64-v8a/libsnailmail.so`, an ELF64 AArch64 library with 16 KiB-aligned PT_LOADs and 12 bionic imports. It is stored in the APK at a 16 KiB-aligned offset.
- **Signing:** v1+v2+v3 with a local debug key.

Expected on-device behaviour (**not observed**; no device was available):
1. The app launches, and `JNIDatInit` logs `SnailMailPort: JNIDatInit: asm.mp3 at offset 58804, 8188993 bytes: 734 records, directory 34701 bytes; index self-check 733 self, 1 shadowed duplicate(s), 0 FAILED`.
2. The GL thread calls `nativeInit`, which throws `IllegalStateException: native entry point … nativeInit (original v7a:0x15650) is not reconstructed yet`.
3. The app closes. There is no game screen, no audio and no gameplay.

## 5. Tests performed

| Command | Result |
|---|---|
| `cmake -S . -B build-host -DSM_SANITIZE=ON && cmake --build build-host && ctest --test-dir build-host` | **9/9 pass** (ASan + UBSan) |
| `cmake -S . -B build-a64 -DCMAKE_TOOLCHAIN_FILE=cmake/aarch64-linux-gnu.toolchain.cmake && … && ctest --test-dir build-a64` | **9/9 pass**, ELF64 AArch64 binaries under qemu-aarch64 8.2.2 |
| `python3 tests/differential/assets/run_diff.py` | **17,894 comparisons, 0 mismatches** vs the original ARM32 code (Unicorn 2.1.4). The suite caught 5 of 6 seeded mutations; the 6th cannot change the output. |
| `tools/android_build/build_dev_apk.sh` | APK built; `apksigner verify` v1/v2/v3 true; `check_elf_alignment.py` OVERALL PASS |
| `check_elf_alignment.py --self-test`, `check_shell_parity.py` | PASS (18 natives, 26 callbacks, 18 exports) |
| worker reproducibility reruns (ELF audit, function index, Ghidra export, censuses) | byte-identical outputs |

The CTest cases:
- `assets.rhash`, `assets.archive_synthetic`, `assets.archive_real`, `assets.extractor_fixture`
- `platform.dat_synthetic`, `platform.dat_real_apk`, `platform.elf_alignment_selftest`, `platform.shell_parity`
- `integration.jni_bridge`: the unmodified `jni_bridge.cpp` driven by a fake JNIEnv, using the real APK's fd and zip offset

### Failed or blocked

- **Gradle `assembleDebug`.** AGP could not be resolved: `403 Forbidden` from `dl.google.com`. The SDK and NDK are unavailable for the same reason.
- **One test expectation of mine was wrong (fixed).** It assumed truncating the archive by 1 byte must fail. Raw records read `decoded_size` bytes, not `stored_size`, so the correct outcome is success.
- **Worker-reported issues, all fixed:**
  - Unicorn CPSR mode banking clobbered SP/LR.
  - Ghidra's default rebase to 0x10000 and hard-float convention; it is now forced to base 0 and softfp.
  - A weak loader differential test was exposed by mutation testing.

### Not run

- Anything on an Android device or emulator: `ARM64_DEVICE_VALIDATED` = 0. There is no 64-bit-only device test and no page-size test on hardware.
- An original-execution capture on a 32-bit device, so there are no traces or screenshots.
- No floating-point routine has been reference-validated yet, and no FP comparison policy is defined.
- The Vulkan/Plume backend: Plume is not present.

## 6. Established vs uncertain

Established (instruction-level evidence):
- archive format and name hash;
- JNI export and callback tables;
- raw JNIEnv/`thiz` caching;
- the `NewGlobalRef`/`DeleteLocalRef` misuse;
- the fixed 16,666 µs timestep and microsecond `nanoTime` clock;
- the GLES 1.1 state subset;
- save files as raw struct images (`asm.cfg` 0x130 B, `of.cfg` 0x2d00 B);
- 711/38/66 chained VFP multiply-accumulates (including conditional forms);
- saturating float→int conversion.

Uncertain:
- thread identities and cross-thread ordering (likely, from framework behaviour);
- bionic-2011 unseeded `rand48` state, which determines `RandTable.bin`;
- `BitmapFactory` premultiplication;
- FPSCR modes on 2011 devices;
- whether a surface recreation during the first two black frames crashes the original (hypothesis);
- the meaning of the extra byte in image `decoded_size`;
- all `.SMO`, TXT, `.X` and table formats.

## 7. Next highest-value action

Reconstruct **`AppInit` state 0**, in this order: `RShellInit`, then
`RMathInit`, then the `cRGame` constructor.

- **`RMathInit` first.** It builds the 128 KiB sin/cos tables and
  `gRMathRand2Table`. It is self-contained and float-sensitive, and every
  simulation routine depends on it. Validating it against the original VFP code
  in the Unicorn harness would set the project's floating-point comparison
  policy, which is required before any physics or camera code can be trusted.
  Evidence needed:
  - bit-exact table output of the original vs the reconstruction;
  - a device-captured `RandTable.bin`, or confirmation of bionic's default `rand48` seed.
- **Then the `cRGame` constructor** (0x3a6468-byte object). It gives the field
  layout that `nativeReInit`, `JNIKey` and `cRGame::AI` write to.
- **In parallel, the `.SMO` format** (364 records) from `cRDirectX::Load` and
  `ObjectProc*`. These are the models `Init0`..`Init5` load.
