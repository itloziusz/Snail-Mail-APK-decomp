# Snail Mail ARM64 preview v0.1.0

This is the first installable preview of the native ARM64 port. The game code
is translated ahead of time and compiled into `lib/arm64-v8a/libsnailmail.so`.
The APK uses the GLES1-on-GLES2 renderer, 60 Hz presentation pacing, and the
new adaptive display settings. It installs as
`com.sandlotgames.snailmail.port`, alongside the original game.

The APK contains the assets from the owner's original 1.00 package. Keep the
original APK out of the repository. Build locally with:

```sh
# Place the original package at original/com.sandlotgames.snailmail-1.00.apk
tools/inventory/setup_workspace.sh
JAVA_HOME=/path/to/jdk SM_APK_VARIANT=aot-gles2 SM_VERSION_CODE=5 \
  tools/android_build/build_dev_apk.sh
```

The output is `work/android_build/out/snailmail-port-arm64-gles2.apk`. Its
application version is `1.00-port-gles2-dev5` and it uses a local debug signing
key. The owner reported gameplay running on a Galaxy S24+ with an earlier
version; the latest display settings and pacing changes have not been verified
on a physical device. Treat this as an experimental build and keep a copy of
any save files before testing updates.

The source, build procedure, known device risks, and evidence levels are in
`docs/PORTING_STATUS.md` and `docs/BUILDING.md`. A production release still
needs a stable release signing key, a build with the Android NDK, and device
validation of rendering, input, audio, lifecycle, and save files.
