# Build Snail Mail for ARM64 from the original APK

This tutorial builds the Android ARM64 port from your own copy of the original
Snail Mail Android 1.00 APK. The Python command verifies that input, extracts
it into a temporary directory, runs the native translator and APK builder, and
checks the finished package. The original APK is never modified.

The tool accepts the known original package with SHA-256
`0d10908d50f2a8361d9bbd3c6c9bff025434fdfb49c0f2b97254a793fb0b29e7`.
Other releases may contain different code or assets and are rejected.

On Windows, follow the [Windows build tutorial](BUILD_ON_WINDOWS.md) to run
these Linux tools in Ubuntu on WSL 2.

## 1. Prepare a Linux build environment

On Debian or Ubuntu, install a JDK, Clang/LLD, the Android packaging tools and
API 23 platform jar, and Python. Package names can vary on other systems.

```sh
sudo apt update
sudo apt install python3 python3-venv python3-tk openjdk-17-jdk clang lld \
  aapt apksigner dalvik-exchange libandroid-23-java
python3 -m venv .venv
.venv/bin/python -m pip install 'capstone==5.0.7' 'pyelftools==0.33'
```

Run these commands at the repository root. The shell build needs `clang`,
`ld.lld`, `javac`, `dalvik-exchange`, `aapt`, `apksigner`, and `keytool` on
`PATH`. If the Android platform jar is elsewhere, set `ANDROID_JAR` to its
absolute path. `JAVA_HOME` is detected from `javac` when it is unset.

## 2. Select the APK and build

On a Linux desktop, run the builder without arguments. In its window, browse
for your original APK and choose the new APK's destination. The version code
and version name can be changed for installed updates; increase the version
code when updating an existing install. Click **Build APK** to start. A
progress indicator and log report the build, and the window can be used for
another build when it finishes. The GUI requires Tkinter and a graphical
desktop session; the build itself runs on Linux, not on the phone.

```sh
.venv/bin/python tools/android_build/build_from_original.py
```

For terminal-only systems or automation, pass the original APK path directly:

```sh
.venv/bin/python tools/android_build/build_from_original.py \
  /path/to/com.sandlotgames.snailmail-1.00.apk \
  --output SnailMail-ARM64-v0.1.1.apk
```

The default output is `SnailMail-ARM64-v0.1.1.apk` in the current directory.
In the desktop window, you can type another path or use **Browse…**.
If two Python builder windows or commands run from the same checkout, they
wait for a repository build lock so their shared native cache and intermediate
APK cannot overwrite one another. Run the Python entry point for concurrent
builds; direct calls to the lower-level shell script do not use this lock.

The builder copies the selected original into a private temporary snapshot
before checking its SHA-256 and extracting it. The destination must end in
`.apk` and be outside `work/android_build`, which contains the intermediate
APK and the local signing key.

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

## Builder validation

The Python command completed three builds on 2026-09-27: one from a clean copy
without generated code or object cache, one repeated default build, and one
with version code 6 and version name `0.1.2`. Each APK reported **Snail Mail**,
the expected version, the original launcher icon, `arm64-v8a` only, a valid
signature, and passing 16 KB alignment checks. A clean checkout generates a
different local signing key; subsequent builds in one checkout reuse its key.

Unsupported input, missing input, an output path equal to the original, and
invalid package or version values were rejected. A failed input validation
left an existing output file untouched. The desktop file dialogs were not run
in the headless build environment; the command-line route and the compiled
Android listener were tested there.

Two simultaneous Python builds with different version codes (9 and 10) also
finished with their own requested identities and valid launcher labels. A
headless Tkinter harness exercised the desktop picker control flow for a
successful build, a failed build, and a canceled file selection; a real
graphical session is still needed to check the appearance of the dialogs.

The expanded GUI was exercised with a headless Tkinter harness for browsing,
build success, build failure, and missing input. Its widget layout still needs
visual inspection on a Linux desktop.

The snapshot build also passed the package, signature, and 16 KB alignment
checks. Attempts to save over the signing key or inside the build workspace
were rejected without changing the key. This verification cannot establish
that the latest APK installs, renders, or handles tilt correctly on a physical
Android device; those checks require a device run.
