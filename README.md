# Snail Mail for Android ARM64

An experimental 64-bit Android port of Sandlot Games' *Snail Mail*. The game
logic from the original Android release is translated ahead of time into native
AArch64 code, while the Android shell retains the game's Java and JNI contract.
The APK contains only `arm64-v8a` native code and renders through a GLES 2
implementation of the game's GLES 1 calls.

## Build from your original APK

The port has a Python builder with a file picker. Run it on a Linux desktop,
choose your original Snail Mail Android 1.00 APK, then choose where to save the
new APK:

```sh
.venv/bin/python tools/android_build/build_from_original.py
```

The same tool accepts an APK path for terminal builds. It verifies the known
input, extracts it temporarily, compiles the native AArch64 game code, and
checks the signed APK. Follow the [step-by-step build tutorial](docs/BUILD_FROM_ORIGINAL.md)
for prerequisites, both ways to build, installation, and update signing. The
app appears as **Snail Mail** with the original launcher icon. It installs under
`com.sandlotgames.snailmail.port.preview` alongside the original game.

The original APK, game assets, and signing key are excluded from this
repository. The earlier downloadable preview release was withdrawn; build a
local APK from your own original copy.

The port includes the GLES 2 renderer, 60 Hz presentation pacing, adaptive
Display settings, immersive full screen, and time-based smoothing for the
accelerometer tilt controls. The owner reported that an earlier ARM64 build
ran on a Galaxy S24+. The newest controls and display changes still need
hands-on device verification; see [porting status](docs/PORTING_STATUS.md).

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
