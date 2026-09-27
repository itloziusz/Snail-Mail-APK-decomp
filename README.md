# Snail Mail — evidence-driven decompilation and native arm64-v8a port

This project reconstructs Sandlot Games' *Snail Mail* for Android
(`com.sandlotgames.snailmail` 1.00, 2011) from the owner-supplied APK. The goal
is a port whose game code runs as native AArch64 machine code, with no ARM32
emulation, translation or 32-bit fallback in the shipped app.

**Status: ARM64 preview.** The ahead-of-time translated game reaches playable
gameplay in the host runner, and an arm64-v8a Android APK can be built from the
owner's original APK. The latest display and GLES2 changes still need an
on-device verification pass. See [`docs/PORTING_STATUS.md`](docs/PORTING_STATUS.md)
and [`RELEASE_NOTES_v0.1.0.md`](RELEASE_NOTES_v0.1.0.md).

| Document | Contents |
|---|---|
| [`docs/PORTING_STATUS.md`](docs/PORTING_STATUS.md) | what is done, tested, failed, not run; next action |
| [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) | decision: native source reconstruction; module and render boundaries |
| [`docs/BOOT_CHAIN.md`](docs/BOOT_CHAIN.md) | launch → JNI → AppInit state machine → fixed-step frame loop |
| [`docs/APK_AUDIT.md`](docs/APK_AUDIT.md), [`docs/JNI_MAP.json`](docs/JNI_MAP.json) | manifest, DEX, signing, dependency graph, full JNI contract |
| [`docs/ASSET_FORMATS.md`](docs/ASSET_FORMATS.md) | `assets/asm.mp3` archive format, recovered from consuming code |
| [`docs/NATIVE_ANALYSIS.md`](docs/NATIVE_ANALYSIS.md) | ELF/Ghidra pipeline, function index, coverage denominators |
| [`docs/PLATFORM_BOUNDARIES.md`](docs/PLATFORM_BOUNDARIES.md), [`docs/ABI_PORTING.md`](docs/ABI_PORTING.md) | GL/audio/input/time/file boundaries; ARM32→AArch64 semantic risks |
| [`docs/BUILDING.md`](docs/BUILDING.md), [`docs/TESTING.md`](docs/TESTING.md) | builds (host, AArch64/qemu, Android, NDK-less dev APK); test policy |
| [`docs/HISTORICAL_LEADS.md`](docs/HISTORICAL_LEADS.md), [`docs/CONVENTIONS.md`](docs/CONVENTIONS.md) | verification of earlier claims; shared conventions |

Layout: `original/` (immutable inputs, not committed), `analysis/` (indices and
manifests; bulk decompiler/extractor output is gitignored), `tools/`
(analysis-only scripts), `reconstructed/` (hand-written, shipping-candidate C),
`android/` (app shell), `tests/`.

Quick start:

```sh
tools/bootstrap/bootstrap_tools.sh          # pinned JADX, apktool, Ghidra
tools/inventory/setup_workspace.sh          # needs original/com.sandlotgames.snailmail-1.00.apk
cmake -S . -B build-host -DSM_SANITIZE=ON && cmake --build build-host && ctest --test-dir build-host
cmake -S . -B build-a64 -DCMAKE_TOOLCHAIN_FILE=cmake/aarch64-linux-gnu.toolchain.cmake \
  && cmake --build build-a64 && ctest --test-dir build-a64
SM_APK_VARIANT=aot-gles2 SM_VERSION_CODE=5 tools/android_build/build_dev_apk.sh
                                             # arm64-only preview APK
```

No proprietary APK, extracted media, decompiled code dumps or signing keys are
committed.
