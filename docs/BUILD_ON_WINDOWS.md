# Build the Snail Mail ARM64 APK on Windows

The builder's compiler and Android packaging steps run on Linux. On Windows,
run them in **Ubuntu on WSL 2**. Windows 11, or Windows 10 build 19044 and
newer, can also display the Python Tk window through WSLg. The command-line
route works without the GUI. These steps have been reviewed against Microsoft's
[WSL installation](https://learn.microsoft.com/windows/wsl/install) and
[Linux GUI app](https://learn.microsoft.com/windows/wsl/tutorials/gui-apps)
guides; the Windows path has not been run in this project's Linux test host.

## 1. Install Ubuntu on WSL 2

Open **PowerShell as Administrator** and run:

```powershell
wsl --install -d Ubuntu
```

Restart Windows if prompted. Open **Ubuntu** from Start and create its Linux
username and password. For an existing WSL installation, update it and restart
its VM from PowerShell:

```powershell
wsl --update
wsl --shutdown
wsl --list --verbose
```

The Ubuntu row must show version `2`. If it shows `1`, run
`wsl --set-version Ubuntu 2` in PowerShell and check again. The GUI needs WSL 2
and current WSLg support; use the command-line builder if no window appears.

## 2. Install build dependencies in Ubuntu

Open Ubuntu and work in its Linux home directory. The repository's generated
objects and signing key will live there; your original APK can be selected
from the Windows Downloads directory through `/mnt/c`.

```sh
sudo apt update
sudo apt install git python3 python3-venv python3-tk openjdk-17-jdk clang lld \
  aapt apksigner dalvik-exchange libandroid-23-java
cd ~
git clone https://github.com/itloziusz/Snail-Mail-ARM64-Builder.git
cd Snail-Mail-ARM64-Builder
python3 -m venv .venv
.venv/bin/python -m pip install 'capstone==5.0.7' 'pyelftools==0.33'
```

If your Ubuntu package mirror lacks a named package, follow the Linux
[dependency details](BUILD_FROM_ORIGINAL.md) for tool locations. The builder
needs `clang`, `ld.lld`, `javac`, `dalvik-exchange`, `aapt`, `apksigner`, and
`keytool` on `PATH` plus the Android API 23 jar.

## 3. Build from your original APK

Get your own original Android 1.00 APK. The accepted file has SHA-256
`0d10908d50f2a8361d9bbd3c6c9bff025434fdfb49c0f2b97254a793fb0b29e7`.
It is not included in the repository.

For the graphical file picker, run this in Ubuntu:

```sh
.venv/bin/python tools/android_build/build_from_original.py
```

In the picker, Windows drives appear under `/mnt/c`, `/mnt/d`, and so on.
Choose the original APK, choose a destination ending in `.apk`, and click
**Build APK**. The window displays the build log and result.

For a terminal build, replace `YourName` with your Windows account folder:

```sh
.venv/bin/python tools/android_build/build_from_original.py \
  "/mnt/c/Users/YourName/Downloads/com.sandlotgames.snailmail-1.00.apk" \
  --output "/mnt/c/Users/YourName/Downloads/SnailMail-ARM64-v0.1.1.apk"
```

Quote paths that contain spaces. The output above lands in Windows Downloads.
The installed Android name is **Snail Mail**; the APK contains `arm64-v8a`
native code only. It is signed with a local development key in
`work/android_build/keys/debug.keystore`. Back up that key privately if you
need a future build to update the same installation. A new key requires
uninstalling the old preview package first. Increase `--version-code` for an
update; see the [Linux tutorial](BUILD_FROM_ORIGINAL.md) for version options
and package checks.

## 4. Install and verify

Copy the APK to an ARM64 Android phone and open it. Android may ask you to
allow installation from the app that opened the file. If ADB is installed and
the phone is connected, `adb install -r` can install it as well. The latest
display, input, and lifecycle changes still need a physical-device test.

If the GUI does not open, run `wsl --update` and `wsl --shutdown` in
PowerShell, reopen Ubuntu, and retry. You can always use the CLI command in
step 3. A Windows `.exe` builder is not supplied.
