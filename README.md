# Snail Mail for Android ARM64

An experimental native 64-bit Android port of Sandlot Games' *Snail Mail*.
This repository includes a Python desktop builder that turns your own copy of
the original Android 1.00 APK into an ARM64 APK. The original game and its
assets are not distributed here.

The port translates the original ARM32 game code ahead of time into native
AArch64 code. It retains the Android Java/JNI interface and renders the game's
GLES 1 graphics through a GLES 2 implementation. The resulting APK has an
`arm64-v8a` native library, the installed name **Snail Mail**, and the original
launcher icon.

## Build the APK

**Host:** Linux with Python 3.10+, a JDK, Clang/LLD, Android API 23 platform
jar, `aapt`, `apksigner`, and `dalvik-exchange`. The Python dependencies are
`capstone==5.0.7` and `pyelftools==0.33`; the desktop GUI also needs Tkinter
and a graphical session. See the [complete setup and build tutorial](docs/BUILD_FROM_ORIGINAL.md)
for package commands and details.

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

For technical detail, see [architecture](docs/ARCHITECTURE.md),
[build internals](docs/BUILDING.md), and [testing](docs/TESTING.md).

This is an independent preservation and compatibility project. *Snail Mail*
and its original artwork and assets belong to their respective rights holders.
Use and distribute game assets only according to the rights you hold.
