# Build Snail Mail for ARM64 from the original APK

This tutorial builds the Android ARM64 port from your own copy of the original
Snail Mail Android 1.00 APK. The Python command verifies that input, extracts
it into a temporary directory, runs the native translator and APK builder, and
checks the finished package. The original APK is never modified.

The tool accepts the known original package with SHA-256
`0d10908d50f2a8361d9bbd3c6c9bff025434fdfb49c0f2b97254a793fb0b29e7`.
Other releases may contain different code or assets and are rejected.

## 1. Prepare a Linux build environment

On Debian or Ubuntu, install a JDK, Clang/LLD, the Android packaging tools and
API 23 platform jar, and Python. Package names can vary on other systems.

```sh
sudo apt update
sudo apt install python3 python3-venv openjdk-17-jdk clang lld \
  aapt apksigner dalvik-exchange libandroid-23-java
python3 -m venv .venv
.venv/bin/python -m pip install 'capstone==5.0.7' 'pyelftools==0.33'
```

Run these commands at the repository root. The shell build needs `clang`,
`ld.lld`, `javac`, `dalvik-exchange`, `aapt`, `apksigner`, and `keytool` on
`PATH`. If the Android platform jar is elsewhere, set `ANDROID_JAR` to its
absolute path. `JAVA_HOME` is detected from `javac` when it is unset.

## 2. Build

```sh
.venv/bin/python tools/android_build/build_from_original.py \
  /path/to/com.sandlotgames.snailmail-1.00.apk \
  --output SnailMail-ARM64-v0.1.1.apk
```

The default output is `SnailMail-ARM64-v0.1.1.apk` in the current directory.
The installed app is **Snail Mail**, package
`com.sandlotgames.snailmail.port.preview`, version name `0.1.1`, version code
`5`. It contains only `arm64-v8a` native code, the original icon and assets,
and the GLES 2 renderer. The script signs it with a local development key and
prints its SHA-256. It checks its package identity, launcher label, signature,
and 16 KB native alignment during the build.

To change the APK version or output path:

```sh
.venv/bin/python tools/android_build/build_from_original.py \
  /path/to/com.sandlotgames.snailmail-1.00.apk \
  --version-code 6 --version-name 0.1.2 \
  --output SnailMail-ARM64-v0.1.2.apk
```

Use a version code higher than the installed one for an update. The signing
key is generated once at `work/android_build/keys/debug.keystore` and reused
on later builds in the same checkout. Keep a private backup of that key if you
want future APKs to update an existing installation without uninstalling it.
A different key cannot update the same package in place.

## 3. Install and check

Transfer the resulting APK to an ARM64 Android device and open it, or use:

```sh
adb install -r SnailMail-ARM64-v0.1.1.apk
aapt dump badging SnailMail-ARM64-v0.1.1.apk | grep -E '^(package:|application-label:|native-code:)'
apksigner verify SnailMail-ARM64-v0.1.1.apk
```

The control input uses the phone's **accelerometer** for tilt steering; the
port now smooths it by sensor time, responding faster to deliberate turns.
Physical-device feel and the latest graphics and lifecycle changes still need
hands-on validation. See [porting status](PORTING_STATUS.md).

The original game APK, extracted assets, and local signing key are not part of
the source repository. Only use and distribute game assets according to the
rights you hold.
