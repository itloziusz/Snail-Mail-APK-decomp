#!/usr/bin/env python3
"""Build the ARM64 Snail Mail preview from the owner's original Android APK.

This is an entry point for the existing native translator and APK builder, not
an on-device APK patcher. It requires the repository and the host build tools.
The original APK and extracted proprietary files remain outside git.
"""

from __future__ import annotations

import argparse
import fcntl
import hashlib
import os
from pathlib import Path
import re
import shutil
import stat
import subprocess
import sys
import tempfile
from typing import Callable
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


def build(args: argparse.Namespace, emit: Callable[[str], None] | None = None) -> Path:
    """Build from a selected APK; emit progress to the CLI or the GUI."""
    if emit is None:
        emit = lambda line: print(line, flush=True)
    if args.original_apk is None:
        raise ValueError("select the original Snail Mail Android 1.00 APK")
    apk = args.original_apk.expanduser().resolve(strict=True)
    output = args.output.expanduser().resolve()
    if not apk.is_file() or not zipfile.is_zipfile(apk):
        raise ValueError("the input must be an Android APK file")
    if output == apk or output == BUILT_APK:
        raise ValueError("output must not overwrite the original APK or the builder's intermediate APK")
    if args.version_code < 1 or args.version_code > 2_100_000_000:
        raise ValueError("--version-code must be a positive Android version code")
    if not re.fullmatch(r"[A-Za-z_][A-Za-z_0-9]*(\.[A-Za-z_][A-Za-z_0-9]*)+", args.app_id):
        raise ValueError("--app-id must be a dotted Android package ID")
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._+-]*", args.version_name):
        raise ValueError("--version-name must contain only letters, numbers, dots, underscores, plus or minus")

    actual = sha256(apk)
    if actual != ORIGINAL_SHA256:
        raise ValueError(f"unsupported original APK: SHA-256 {actual}; expected {ORIGINAL_SHA256}")

    emit(f"Verified original APK: {actual}")
    work = REPO / "work"
    work.mkdir(exist_ok=True)
    # The native object cache, app staging directory, and intermediate APK are
    # shared by every invocation. Hold this lock through validation and copy.
    with (work / ".apk-build.lock").open("w") as lock:
        emit("Waiting for the repository build lock…")
        fcntl.flock(lock, fcntl.LOCK_EX)
        emit("Build lock acquired")
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
            with subprocess.Popen([str(REPO / "tools/android_build/build_dev_apk.sh")],
                                  cwd=REPO, env=env, stdout=subprocess.PIPE,
                                  stderr=subprocess.STDOUT, text=True, bufsize=1) as process:
                assert process.stdout is not None
                for line in process.stdout:
                    emit(line.rstrip("\n"))
                if process.wait() != 0:
                    raise RuntimeError("Android build failed; see the log above")
        badging = subprocess.check_output(["aapt", "dump", "badging", str(BUILT_APK)], text=True)
        package = re.search(r"^package: name='([^']+)' versionCode='(\d+)' versionName='([^']+)'", badging, re.M)
        label = re.search(r"^application-label:'([^']+)'", badging, re.M)
        if not package or package.groups() != (args.app_id, str(args.version_code), args.version_name):
            raise RuntimeError("built APK package or version does not match the requested identity")
        if not label or label.group(1) != "Snail Mail":
            raise RuntimeError("built APK launcher name is not Snail Mail")
        subprocess.run(["apksigner", "verify", str(BUILT_APK)], check=True)
        output.parent.mkdir(parents=True, exist_ok=True)
        descriptor, temporary = tempfile.mkstemp(prefix=".snailmail-", suffix=".apk", dir=output.parent)
        os.close(descriptor)
        try:
            shutil.copyfile(BUILT_APK, temporary)
            os.replace(temporary, output)
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)
    emit(f"Built {output}\nSHA-256 {sha256(output)}\nInstalled name: Snail Mail")
    return output


def browse_and_build(args: argparse.Namespace) -> int:
    """Choose the original and destination with desktop dialogs, then build."""
    try:
        import queue
        import threading
        import tkinter as tk
        from tkinter import filedialog, messagebox, scrolledtext, ttk
        root = tk.Tk()
    except Exception as error:
        raise RuntimeError("a desktop with Tkinter is required for the file picker; "
                           "pass the original APK path on the command line instead") from error

    root.withdraw()
    selected = filedialog.askopenfilename(
        parent=root, title="Select your original Snail Mail Android 1.00 APK",
        filetypes=[("Android APK", "*.apk"), ("All files", "*")])
    if not selected:
        root.destroy()
        return 0
    destination = filedialog.asksaveasfilename(
        parent=root, title="Save the new ARM64 APK", defaultextension=".apk",
        initialfile="SnailMail-ARM64-v0.1.1.apk",
        filetypes=[("Android APK", "*.apk")])
    if not destination:
        root.destroy()
        return 0
    args.original_apk, args.output = Path(selected), Path(destination)
    root.title("Snail Mail ARM64 Builder")
    root.geometry("760x440")
    frame = ttk.Frame(root, padding=12)
    frame.pack(fill="both", expand=True)
    ttk.Label(frame, text="Building Snail Mail for ARM64…").pack(anchor="w")
    ttk.Label(frame, text=f"Original: {selected}", wraplength=730).pack(anchor="w")
    ttk.Label(frame, text=f"Output: {destination}", wraplength=730).pack(anchor="w")
    log = scrolledtext.ScrolledText(frame, state="disabled", height=20)
    log.pack(fill="both", expand=True, pady=(10, 0))
    messages: queue.Queue[tuple[str, str]] = queue.Queue()
    finished = False
    exit_status = 0

    def close_window() -> None:
        if finished:
            root.destroy()
        else:
            messagebox.showinfo("Build in progress", "Wait for the APK build to finish.", parent=root)

    root.protocol("WM_DELETE_WINDOW", close_window)

    def worker() -> None:
        try:
            result = build(args, lambda line: messages.put(("log", line)))
        except (OSError, ValueError, RuntimeError, subprocess.CalledProcessError) as error:
            messages.put(("error", str(error)))
        else:
            messages.put(("done", str(result)))

    def show_progress() -> None:
        nonlocal finished, exit_status
        while not messages.empty():
            kind, value = messages.get_nowait()
            if kind == "log":
                log.configure(state="normal")
                log.insert("end", value + "\n")
                log.see("end")
                log.configure(state="disabled")
            elif kind == "error":
                finished = True
                exit_status = 1
                root.title("Snail Mail ARM64 Builder — failed")
                messagebox.showerror("Build failed", value, parent=root)
            else:
                finished = True
                root.title("Snail Mail ARM64 Builder — complete")
                messagebox.showinfo("Build complete", f"New APK saved at:\n{value}", parent=root)
        if not finished:
            root.after(100, show_progress)

    root.deiconify()
    threading.Thread(target=worker, daemon=True).start()
    root.after(100, show_progress)
    root.mainloop()
    return exit_status


def run() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("original_apk", nargs="?", type=Path,
                        help="original Snail Mail Android 1.00 APK; omit to browse for it")
    parser.add_argument("--output", type=Path, default=Path("SnailMail-ARM64-v0.1.1.apk"),
                        help="destination for the newly signed APK (default: ./SnailMail-ARM64-v0.1.1.apk)")
    parser.add_argument("--version-code", type=int, default=5,
                        help="Android version code; increase this for installed updates (default: 5)")
    parser.add_argument("--version-name", default="0.1.1", help="displayed app version (default: 0.1.1)")
    parser.add_argument("--app-id", default=DEFAULT_APP_ID, help=f"Android package ID (default: {DEFAULT_APP_ID})")
    args = parser.parse_args()
    if args.original_apk is None:
        return browse_and_build(args)
    build(args)
    return 0


if __name__ == "__main__":
    try:
        sys.exit(run())
    except (OSError, ValueError, RuntimeError, subprocess.CalledProcessError) as error:
        print(f"build_from_original: {error}", file=sys.stderr)
        sys.exit(1)
