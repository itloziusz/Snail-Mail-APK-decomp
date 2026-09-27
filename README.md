# Snail Mail for Android ARM64

An experimental 64-bit Android port of Sandlot Games' *Snail Mail*. The game
logic from the original Android release is translated ahead of time into native
AArch64 code, while the Android shell retains the game's Java and JNI contract.
The APK contains only `arm64-v8a` native code and renders through a GLES 2
implementation of the game's GLES 1 calls.

## Current release

**v0.1.0 is an installable preview.** It appears on the launcher as **Snail
Mail 64-bit** with the original icon. It installs under
`com.sandlotgames.snailmail.port.preview`, so it can coexist with the original
game and earlier port test builds. The shell requests immersive full screen,
including the navigation bar, and limits drawing to the game's intended 60 Hz
pace. The in-game Display page adds settings for modern screen shapes.

The owner reported that an earlier ARM64 build ran on a Galaxy S24+. The latest
GLES 2, display, navigation bar, and launcher changes have been built and
checked locally, but have not yet been confirmed on a physical device. See
[release notes](RELEASE_NOTES_v0.1.0.md) and the
[porting status](docs/PORTING_STATUS.md) for specific test results and limits.

## Build the preview APK

You need your own copy of the original Android 1.00 APK. Place it at
`original/com.sandlotgames.snailmail-1.00.apk`. The build extracts its game
assets and launcher icons locally; those files and signing keys are excluded
from this repository.

On a Linux machine with the tools described in [building](docs/BUILDING.md):

```sh
tools/inventory/setup_workspace.sh
JAVA_HOME=/path/to/jdk SM_APK_VARIANT=aot-gles2 SM_VERSION_CODE=3 \
  SM_VERSION_NAME=0.1.0 \
  SM_APP_ID=com.sandlotgames.snailmail.port.preview \
  tools/android_build/build_dev_apk.sh
```

The signed APK is written to
`work/android_build/out/snailmail-port-arm64-gles2.apk`. This development build
uses a locally generated key. To update an installed preview without losing
its app data, sign the next APK with the same key and increase its version code.
The Gradle project in `android/` is a separate development path; the published
preview was built with the script above.

## How the port works

The original ARM32 game functions are translated to C ahead of time by
`tools/aot/arm2c.py` and compiled into `libsnailmail.so` for AArch64. The
`aot/runtime/` layer handles calls into Android and the original asset archive.
`reconstructed/rendering/` maps the needed fixed-function graphics calls to
GLES 2. QEMU and Unicorn are used for local verification only; neither is
included in the APK.

The project also contains reference analysis, unit and differential tests, and
an Android shell. Start with [architecture](docs/ARCHITECTURE.md),
[building](docs/BUILDING.md), and [testing](docs/TESTING.md) for details.

This is an independent preservation and compatibility project. *Snail Mail*
and its original artwork and assets belong to their respective rights holders.
