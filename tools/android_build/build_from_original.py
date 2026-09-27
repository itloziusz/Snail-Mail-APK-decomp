#!/usr/bin/env python3
"""Build the ARM64 Snail Mail preview from the owner's original Android APK.

This is an entry point for the existing native translator and APK builder, not
an on-device APK patcher. It requires the repository and the host build tools.
The original APK and extracted proprietary files remain outside git.
"""

from __future__ import annotations

import argparse
import hashlib
import os
from pathlib import Path
import re
import shutil
import stat
import subprocess
import sys
import tempfile
import zipfile


REPO = Path(__file__).resolve().parents[2]
ORIGINAL_SHA256 = "0d10908d50f2a8361d9bbd3c6c9bff025434fdfb49c0f2b97254a793fb0b29e7"
DEFAULT_APP_ID = "com.sandlotgames.snailmail.port.preview"
BUILT_APK = REPO / "work/android_build/out/snailmail-port-arm64-gles2.apk"
REQUIRED = (
    "assets/asm.mp3",
    "lib/armeabi-v7a/libsnailmail.so",
    "res/drawable-ldpi/icon.png",
    "res/drawable-mdpi/icon.png",
    "res/drawable-hdpi/icon.png",
)


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def extract_original(apk: Path, target: Path) -> None:
    """Extract a verified APK into this build's private temporary directory."""
    with zipfile.ZipFile(apk) as archive:
        names = set(archive.namelist())
        missing = set(REQUIRED) - names
        if missing:
            raise ValueError(f"Original APK is missing: {', '.join(sorted(missing))}")
        for entry in archive.infolist():
            name = entry.filename
            parts = Path(name).parts
            mode = entry.external_attr >> 16
            if (not name or name.startswith("/") or "\\" in name or ".." in parts
                    or stat.S_ISLNK(mode)):
                raise ValueError(f"Unsafe APK entry: {name!r}")
        archive.extractall(target)


def run() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("original_apk", type=Path, help="owner's original Snail Mail Android 1.00 APK")
    parser.add_argument("--output", type=Path, default=Path("SnailMail-ARM64-v0.1.1.apk"),
                        help="destination for the newly signed APK (default: ./SnailMail-ARM64-v0.1.1.apk)")
    parser.add_argument("--version-code", type=int, default=5,
                        help="Android version code; increase this for installed updates (default: 5)")
    parser.add_argument("--version-name", default="0.1.1", help="displayed app version (default: 0.1.1)")
    parser.add_argument("--app-id", default=DEFAULT_APP_ID, help=f"Android package ID (default: {DEFAULT_APP_ID})")
    args = parser.parse_args()

    apk = args.original_apk.expanduser().resolve(strict=True)
    output = args.output.expanduser().resolve()
    if not apk.is_file() or not zipfile.is_zipfile(apk):
        parser.error("the input must be an Android APK file")
    if output == apk or output == BUILT_APK:
        parser.error("output must not overwrite the original APK or the builder's intermediate APK")
    if args.version_code < 1 or args.version_code > 2_100_000_000:
        parser.error("--version-code must be a positive Android version code")
    if not re.fullmatch(r"[A-Za-z_][A-Za-z_0-9]*(\.[A-Za-z_][A-Za-z_0-9]*)+", args.app_id):
        parser.error("--app-id must be a dotted Android package ID")
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._+-]*", args.version_name):
        parser.error("--version-name must contain only letters, numbers, dots, underscores, plus or minus")

    actual = sha256(apk)
    if actual != ORIGINAL_SHA256:
        parser.error(f"unsupported original APK: SHA-256 {actual}; expected {ORIGINAL_SHA256}")

    print(f"Verified original APK: {actual}", flush=True)
    work = REPO / "work"
    work.mkdir(exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="snailmail-apk-", dir=work) as temp:
        extracted = Path(temp)
        extract_original(apk, extracted)
        env = os.environ.copy()
        if not env.get("JAVA_HOME"):
            javac = shutil.which("javac")
            if not javac:
                raise RuntimeError("javac is required; install a JDK or set JAVA_HOME")
            env["JAVA_HOME"] = str(Path(javac).resolve().parent.parent)
        env.update(SM_APK_VARIANT="aot-gles2", SM_APP_ID=args.app_id,
                   SM_VERSION_CODE=str(args.version_code), SM_VERSION_NAME=args.version_name,
                   SM_EXTRACTED_DIR=str(extracted))
        subprocess.run([str(REPO / "tools/android_build/build_dev_apk.sh")],
                       cwd=REPO, env=env, check=True)
        output.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(BUILT_APK, output)
    badging = subprocess.check_output(["aapt", "dump", "badging", str(output)], text=True)
    package = re.search(r"^package: name='([^']+)' versionCode='(\d+)' versionName='([^']+)'", badging, re.M)
    label = re.search(r"^application-label:'([^']+)'", badging, re.M)
    if not package or package.groups() != (args.app_id, str(args.version_code), args.version_name):
        raise RuntimeError("built APK package or version does not match the requested identity")
    if not label or label.group(1) != "Snail Mail":
        raise RuntimeError("built APK launcher name is not Snail Mail")
    subprocess.run(["apksigner", "verify", str(output)], check=True)
    print(f"Built {output}\nSHA-256 {sha256(output)}\nInstalled name: Snail Mail", flush=True)
    return 0


if __name__ == "__main__":
    try:
        sys.exit(run())
    except (OSError, ValueError, RuntimeError, subprocess.CalledProcessError) as error:
        print(f"build_from_original: {error}", file=sys.stderr)
        sys.exit(1)
