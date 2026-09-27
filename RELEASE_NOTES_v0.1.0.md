# Snail Mail ARM64 preview v0.1.0

This is the first installable preview of the native ARM64 port. The game code
is translated ahead of time and compiled into `lib/arm64-v8a/libsnailmail.so`.
The APK uses the GLES1-on-GLES2 renderer, 60 Hz presentation pacing, and the
new adaptive display settings. The corrected download installs as
`com.sandlotgames.snailmail.port.preview`, alongside the original game and
earlier `com.sandlotgames.snailmail.port` test builds. It avoids the signing
conflict that can produce the generic “App not installed” error when an older
test build used a different debug key. The existing test build and its save
files are left alone.

The installed app is named “Snail Mail 64-bit” and uses the original launcher icon in ldpi, mdpi, and hdpi. The APK contains the assets from the owner's original 1.00 package. The build stages the icon from that package; no proprietary art is checked into the source repository. Keep the
original APK out of the repository. Build locally with:

```sh
# Place the original package at original/com.sandlotgames.snailmail-1.00.apk
tools/inventory/setup_workspace.sh
JAVA_HOME=/path/to/jdk SM_APK_VARIANT=aot-gles2 SM_VERSION_CODE=3 \
  SM_VERSION_NAME=0.1.0 \
  SM_APP_ID=com.sandlotgames.snailmail.port.preview \
  tools/android_build/build_dev_apk.sh
```

The output is `work/android_build/out/snailmail-port-arm64-gles2.apk` (SHA-256 `3bec67c224f52ff687969cdf36f97cb47d38506043ab98637d8c12f8e76c23df`). Its
application version is `0.1.0` and it uses the same local debug signing
key. The owner reported gameplay running on a Galaxy S24+ with an earlier
version; the latest display settings and pacing changes have not been verified
on a physical device. Treat this as an experimental build and keep a copy of
any save files before testing updates.

Version code 3 requests immersive mode for both the status bar and navigation
bar. The Activity reapplies it when resumed or refocused. Android can still
show the bars briefly when the user swipes from the screen edge.

The source, build procedure, known device risks, and evidence levels are in
`docs/PORTING_STATUS.md` and `docs/BUILDING.md`. A production release still
needs a stable release signing key, a build with the Android NDK, and device
validation of rendering, input, audio, lifecycle, and save files.
