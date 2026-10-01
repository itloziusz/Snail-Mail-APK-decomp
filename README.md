# Snail Mail ARM64 Builder

A Python desktop GUI and CLI for building an experimental native 64-bit
Android port of Sandlot Games' *Snail Mail* from your own original Android
1.00 APK. The original game and its assets are not distributed here.

The port translates the original ARM32 game code ahead of time into native
AArch64 code. It retains the Android Java/JNI interface and renders the game's
GLES 1 graphics through a GLES 2 implementation. The resulting APK has an
`arm64-v8a` native library, the installed name **Snail Mail**, and the original
launcher icon.

## Build the APK

**Host:** Linux, or Ubuntu on Windows through WSL 2, with Python 3.10+, a JDK, Clang/LLD, Android API 23 platform
jar, `aapt`, `apksigner`, and `dalvik-exchange`. The Python dependencies are
`capstone==5.0.7` and `pyelftools==0.33`; the desktop GUI also needs Tkinter
and a graphical session. See the [Linux setup and build tutorial](docs/BUILD_FROM_ORIGINAL.md)
or the [Windows tutorial](docs/BUILD_ON_WINDOWS.md) for package commands and details.

1. Get your own original Snail Mail Android 1.00 APK. The builder accepts the
   known original with SHA-256
   `0d10908d50f2a8361d9bbd3c6c9bff025434fdfb49c0f2b97254a793fb0b29e7`.
2. At the repository root, launch the desktop builder:

   ```sh
   .venv/bin/python tools/android_build/build_from_original.py
   ```

3. Browse for the original APK, choose where to save the new APK, and click
   **Build APK**. The window lets you set a version code/name and shows the
   live build log. Increase the version code for an installed update.

For a terminal or automated build, pass the input path:

```sh
.venv/bin/python tools/android_build/build_from_original.py \
  /path/to/com.sandlotgames.snailmail-1.00.apk \
  --output SnailMail-ARM64-v0.1.1.apk
```

The builder verifies the input, uses a private temporary snapshot, compiles
the native code, signs the APK with a local development key, and checks the
package name, installed label, signature, and 16 KB native alignment. The
default package ID is `com.sandlotgames.snailmail.port.preview`, so it can
install beside the original game. Keep a private backup of
`work/android_build/keys/debug.keystore` if future builds must update the same
installation. An APK signed with a different key cannot update it in place.

## Native iOS port (bootstrap)

A native iPhone/iPad target now lives under `ios/`. It reuses the same AOT-translated game core, replaces the Android shell with UIKit/CoreMotion/AVFoundation services, and keeps the original APK assets plus generated AOT code local and untracked. See [docs/IOS_PORT.md](docs/IOS_PORT.md) for the Xcode workflow and current validation status.

## Install and status

Copy the resulting APK to an ARM64 Android device and open it, or install it
with `adb install -r SnailMail-ARM64-v0.1.1.apk` if ADB is available. Android
may ask you to allow installation from the app that opens the APK.

The current port includes GLES 2 rendering, 60 Hz presentation pacing,
immersive full screen, adaptive Display settings, and time-based smoothing
for accelerometer steering. Local builds pass package, signature, architecture,
and alignment checks. The owner reported an earlier ARM64 build running on a
Galaxy S24+; the latest controls and display changes still require a physical
device test. See [porting status](docs/PORTING_STATUS.md) for evidence and
known risks. The earlier public preview release was withdrawn; build locally
from your own original APK.

## Project layout

| Path | Purpose |
| --- | --- |
| `tools/android_build/build_from_original.py` | Desktop GUI and command-line build entry point |
| `tools/android_build/build_dev_apk.sh` | Native compilation and APK packaging |
| `tools/aot/`, `aot/` | Ahead-of-time translation and AArch64 runtime |
| `android/` | Android shell, resources, and JNI bridge |
| `reconstructed/rendering/` | GLES 1 calls implemented over GLES 2 |
| `tests/`, `tools/validation/` | Unit, differential, and platform checks |
| `tools/recompiler32/` | Standalone experimental ARM32-to-C recompiler starter |
| `analysis/native/RECOMPILER_REVIEW.*` | Per-function ARM32 census and AOT review ledger |

## Recompiler starter for developers

The repository also has a [documented experimental recompiler](docs/RECOMPILER_STARTER.md)
for a small subset of raw 32-bit ARM instructions. Its helpers extract named
ELF32 ARM functions, report instruction coverage, compile generated C, and
compare results with known test vectors. It is a
starting point for exploring another game's port, not an automatic converter
for arbitrary games or an APK builder. The Snail Mail build uses the more
complete game-specific translator in `tools/aot/arm2c.py`.

The starter can also export an **editable Visual Studio 2022 x64 project**:

```sh
python3 tools/recompiler32/recompile.py /path/to/function.a32 \
  --base 0x1000 --vs-project /path/to/NewProject
```

Its generated C is a normal source file you can change by hand. The exporter
never overwrites an existing project directory; see the
[Visual Studio walkthrough](docs/RECOMPILER_STARTER.md#edit-the-translation-in-visual-studio).

The [function review](docs/RECOMPILER_REVIEW.md) cross-checks all 1,173 unique
game-library `.text` starts and 94 separate import stubs against the original
ELF, unwind table, function index, and AOT manifest. It identifies unknown
behavior and untested translations explicitly, with a next action per function.

For technical detail, see [architecture](docs/ARCHITECTURE.md),
[build internals](docs/BUILDING.md), and [testing](docs/TESTING.md).

This is an independent preservation and compatibility project. *Snail Mail*
and its original artwork and assets belong to their respective rights holders.
Use and distribute game assets only according to the rights you hold.

🎉 **Snail Mail is saved!** The little ship has crossed from 32-bit history
into a new ARM64 harbor. Here's to everyone keeping old games playable, one
careful build at a time. 🐌✉️
