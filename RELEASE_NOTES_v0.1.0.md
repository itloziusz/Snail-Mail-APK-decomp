# Historical preview notes — v0.1.0

The first downloadable ARM64 preview was published and later withdrawn. No
binary release is currently published in this repository. This file preserves
what the v0.1.0 preview contained; it is not a current download page.

The APK contained native AArch64 game code translated ahead of time from the
original Android version, a GLES1-on-GLES2 renderer, 60 Hz presentation pacing,
adaptive Display settings, immersive full screen, and the original launcher
icon. It used package `com.sandlotgames.snailmail.port.preview` to coexist with
the original game. Its local debug signing key was not committed.

The current source adds time-based smoothing to accelerometer tilt steering and
provides a Python builder with a file picker. To make your own APK from the
original Android 1.00 package, follow [the build tutorial](docs/BUILD_FROM_ORIGINAL.md).
It verifies the exact known original APK and builds version `0.1.1` with
versionCode `5` and installed name **Snail Mail**.

The earlier Galaxy S24+ gameplay report concerns an older build. The current
renderer, display, lifecycle, and steering changes still need hands-on device
validation. See [porting status](docs/PORTING_STATUS.md) for evidence and limits.
