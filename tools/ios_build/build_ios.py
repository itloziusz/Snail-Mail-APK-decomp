#!/usr/bin/env python3
"""Prepare and optionally build the Snail Mail native iOS port from an owned 1.00 APK.

The original APK and translated AOT output remain local and are never committed.
On macOS this script can generate an Xcode project and optionally invoke its build.
"""
from __future__ import annotations

import argparse
import hashlib
from pathlib import Path
import shutil
import subprocess
import sys
import zipfile

REFERENCE_APK_SHA256 = "0d10908d50f2a8361d9bbd3c6c9bff025434fdfb49c0f2b97254a793fb0b29e7"


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def run(cmd: list[str], cwd: Path) -> None:
    print("+", " ".join(map(str, cmd)))
    subprocess.run(cmd, cwd=cwd, check=True)


def extract_original(apk: Path, extracted: Path) -> None:
    extracted.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(apk) as z:
        needed = "lib/armeabi-v7a/libsnailmail.so"
        if needed not in z.namelist() or "assets/asm.mp3" not in z.namelist():
            raise SystemExit("APK is missing the expected Snail Mail v7a library or assets/asm.mp3")
        lib_out = extracted / needed
        lib_out.parent.mkdir(parents=True, exist_ok=True)
        lib_out.write_bytes(z.read(needed))
        raw_assets = extracted / "assets"
        raw_assets.mkdir(exist_ok=True)
        for info in z.infolist():
            if info.filename.startswith("assets/") and not info.is_dir():
                (raw_assets / Path(info.filename).name).write_bytes(z.read(info.filename))


def prepare_audio(raw_assets: Path, ios_assets: Path, no_transcode: bool) -> tuple[int, int]:
    ios_assets.mkdir(parents=True, exist_ok=True)
    shutil.copy2(raw_assets / "asm.mp3", ios_assets / "asm.mp3")
    oggs = sorted(raw_assets.glob("*.ogg"))
    ffmpeg = None if no_transcode else shutil.which("ffmpeg")
    converted = copied = 0
    if not ffmpeg and oggs:
        print("warning: ffmpeg not found; keeping Ogg Vorbis files. AVAudioPlayer may not decode them on all iOS versions.")
    for i, src in enumerate(oggs, 1):
        if ffmpeg:
            dst = ios_assets / (src.stem + ".m4a")
            if not dst.exists() or dst.stat().st_mtime < src.stat().st_mtime:
                subprocess.run([
                    ffmpeg, "-hide_banner", "-loglevel", "error", "-y", "-i", str(src),
                    "-vn", "-c:a", "aac", "-b:a", "96k", str(dst)
                ], check=True)
            converted += 1
        else:
            shutil.copy2(src, ios_assets / src.name)
            copied += 1
        if i % 25 == 0 or i == len(oggs):
            print(f"audio: {i}/{len(oggs)}")
    return converted, copied


def main() -> int:
    ap = argparse.ArgumentParser(description="Prepare/generate the Snail Mail iOS port from the original APK")
    ap.add_argument("apk", type=Path, help="your original Snail Mail Android 1.00 APK")
    ap.add_argument("--allow-unknown-apk", action="store_true",
                    help="continue if the APK hash differs from the verified reference")
    ap.add_argument("--no-audio-transcode", action="store_true",
                    help="bundle Ogg files directly instead of using ffmpeg to make AAC/M4A")
    ap.add_argument("--configure-only", action="store_true",
                    help="on macOS, generate the Xcode project but do not build")
    ap.add_argument("--build", action="store_true",
                    help="on macOS, build the Release target after generating Xcode")
    ap.add_argument("--team", default="", help="Apple Development Team ID to put in the generated Xcode project")
    args = ap.parse_args()

    script = Path(__file__).resolve()
    root = script.parents[2]
    apk = args.apk.resolve()
    if not apk.is_file():
        raise SystemExit(f"APK not found: {apk}")
    got = sha256(apk)
    print("APK SHA-256:", got)
    if got != REFERENCE_APK_SHA256 and not args.allow_unknown_apk:
        raise SystemExit("APK hash differs from the validated 1.00 reference; use --allow-unknown-apk only if you intend to revalidate it")

    work = root / "work" / "ios_build"
    extracted = work / "original"
    ios_assets = work / "assets"
    extract_original(apk, extracted)

    try:
        import capstone  # noqa: F401
        import elftools  # noqa: F401
    except ImportError:
        raise SystemExit("Missing Python dependencies. Install: pip install capstone==5.0.7 pyelftools==0.33")

    original_so = extracted / "lib" / "armeabi-v7a" / "libsnailmail.so"
    generated = root / "aot" / "generated"
    generated.mkdir(parents=True, exist_ok=True)
    run([sys.executable, str(root / "tools/aot/arm2c.py"), "--elf", str(original_so),
         "--out", str(generated)], root)

    converted, copied = prepare_audio(extracted / "assets", ios_assets, args.no_audio_transcode)
    print(f"prepared assets: asm.mp3 + {converted} AAC/M4A + {copied} Ogg")

    if sys.platform != "darwin":
        print("iOS source/AOT/assets are prepared. Xcode generation requires macOS with Xcode installed.")
        return 0

    cmake = shutil.which("cmake")
    if not cmake:
        raise SystemExit("cmake not found (install CMake, e.g. with Homebrew)")
    build = work / "xcode"
    cmd = [
        cmake, "-S", str(root), "-B", str(build), "-G", "Xcode",
        "-DCMAKE_SYSTEM_NAME=iOS", "-DCMAKE_OSX_SYSROOT=iphoneos",
        "-DCMAKE_OSX_ARCHITECTURES=arm64", "-DCMAKE_OSX_DEPLOYMENT_TARGET=15.0",
        "-DSM_BUILD_TESTS=OFF", "-DSM_BUILD_IOS_APP=ON",
        f"-DSM_IOS_ASSETS_DIR={ios_assets}",
    ]
    if args.team:
        cmd.append(f"-DCMAKE_XCODE_ATTRIBUTE_DEVELOPMENT_TEAM={args.team}")
    run(cmd, root)
    print("Xcode project:", build / "snailmail_reconstructed.xcodeproj")

    if args.build and not args.configure_only:
        run([cmake, "--build", str(build), "--config", "Release", "--target", "SnailMailIOS"], root)
        print("Built iOS app under:", build / "ios" / "Release-iphoneos")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
