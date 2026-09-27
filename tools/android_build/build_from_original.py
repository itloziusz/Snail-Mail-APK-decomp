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
    if output == apk:
        raise ValueError("output must not overwrite the original APK")
    if output.suffix.lower() != ".apk":
        raise ValueError("output must be an .apk file")
    if output.is_relative_to(REPO / "work/android_build"):
        raise ValueError("output must be outside the builder's working directory")
    if args.version_code < 1 or args.version_code > 2_100_000_000:
        raise ValueError("--version-code must be a positive Android version code")
    if not re.fullmatch(r"[A-Za-z_][A-Za-z_0-9]*(\.[A-Za-z_][A-Za-z_0-9]*)+", args.app_id):
        raise ValueError("--app-id must be a dotted Android package ID")
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._+-]*", args.version_name):
        raise ValueError("--version-name must contain only letters, numbers, dots, underscores, plus or minus")

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
            # Read the original only once. A user moving or changing the
            # selected file cannot swap it between verification and extraction.
            snapshot = extracted / "original.apk"
            shutil.copyfile(apk, snapshot)
            actual = sha256(snapshot)
            if actual != ORIGINAL_SHA256:
                raise ValueError(f"unsupported original APK: SHA-256 {actual}; expected {ORIGINAL_SHA256}")
            emit(f"Verified original APK: {actual}")
            source_dir = extracted / "source"
            source_dir.mkdir()
            extract_original(snapshot, source_dir)
            env = os.environ.copy()
            if not env.get("JAVA_HOME"):
                javac = shutil.which("javac")
                if not javac:
                    raise RuntimeError("javac is required; install a JDK or set JAVA_HOME")
                env["JAVA_HOME"] = str(Path(javac).resolve().parent.parent)
            env.update(SM_APK_VARIANT="aot-gles2", SM_APP_ID=args.app_id,
                       SM_VERSION_CODE=str(args.version_code), SM_VERSION_NAME=args.version_name,
                       SM_EXTRACTED_DIR=str(source_dir))
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
    """Run the desktop builder with file browsing and build progress."""
    try:
        import queue
        import threading
        import tkinter as tk
        from tkinter import filedialog, messagebox, scrolledtext, ttk
        root = tk.Tk()
    except Exception as error:
        raise RuntimeError("a desktop with Tkinter is required for the file picker; "
                           "pass the original APK path on the command line instead") from error

    root.title("Snail Mail ARM64 Builder")
    root.geometry("820x560")
    frame = ttk.Frame(root, padding=12)
    frame.pack(fill="both", expand=True)
    frame.columnconfigure(1, weight=1)
    frame.rowconfigure(5, weight=1)

    original_value = tk.StringVar(value=str(args.original_apk or ""))
    output_value = tk.StringVar(value=str(args.output))
    version_code_value = tk.StringVar(value=str(args.version_code))
    version_name_value = tk.StringVar(value=args.version_name)
    status_value = tk.StringVar(value="Select your original APK, then build.")
    suggested_name = f"SnailMail-ARM64-v{args.version_name}.apk"

    def update_suggested_output(*_unused: object) -> None:
        nonlocal suggested_name
        version = version_name_value.get()
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._+-]*", version):
            return
        new_name = f"SnailMail-ARM64-v{version}.apk"
        current = Path(output_value.get())
        if current.name == suggested_name:
            output_value.set(str(current.with_name(new_name)))
        suggested_name = new_name

    version_name_value.trace_add("write", update_suggested_output)

    ttk.Label(frame, text="Original Android 1.00 APK").grid(row=0, column=0, sticky="w", pady=4)
    original_entry = ttk.Entry(frame, textvariable=original_value)
    original_entry.grid(row=0, column=1, sticky="ew", padx=8)

    def choose_original() -> None:
        selected = filedialog.askopenfilename(
            parent=root, title="Select your original Snail Mail Android 1.00 APK",
            filetypes=[("Android APK", "*.apk"), ("All files", "*")])
        if selected:
            original_value.set(selected)

    original_button = ttk.Button(frame, text="Browse…", command=choose_original)
    original_button.grid(row=0, column=2)

    ttk.Label(frame, text="New APK").grid(row=1, column=0, sticky="w", pady=4)
    output_entry = ttk.Entry(frame, textvariable=output_value)
    output_entry.grid(row=1, column=1, sticky="ew", padx=8)

    def choose_output() -> None:
        current = Path(output_value.get().strip() or "SnailMail-ARM64-v0.1.1.apk").expanduser()
        selected = filedialog.asksaveasfilename(
            parent=root, title="Save the new ARM64 APK", defaultextension=".apk",
            initialfile=current.name, filetypes=[("Android APK", "*.apk")])
        if selected:
            output_value.set(selected)

    output_button = ttk.Button(frame, text="Browse…", command=choose_output)
    output_button.grid(row=1, column=2)

    ttk.Label(frame, text="Version code").grid(row=2, column=0, sticky="w", pady=4)
    code_entry = ttk.Entry(frame, textvariable=version_code_value, width=12)
    code_entry.grid(row=2, column=1, sticky="w", padx=8)
    ttk.Label(frame, text="Increase this to update an installed build.").grid(row=2, column=2, sticky="w")
    ttk.Label(frame, text="Version name").grid(row=3, column=0, sticky="w", pady=4)
    name_entry = ttk.Entry(frame, textvariable=version_name_value, width=20)
    name_entry.grid(row=3, column=1, sticky="w", padx=8)

    controls = [original_entry, original_button, output_entry, output_button, code_entry, name_entry]
    actions = ttk.Frame(frame)
    actions.grid(row=4, column=0, columnspan=3, sticky="ew", pady=(8, 4))
    ttk.Label(actions, textvariable=status_value).pack(side="left")
    progress = ttk.Progressbar(actions, mode="indeterminate", length=120)
    progress.pack(side="right", padx=(8, 0))

    log = scrolledtext.ScrolledText(frame, state="disabled", height=20)
    log.grid(row=5, column=0, columnspan=3, sticky="nsew", pady=(6, 8))
    messages: queue.Queue[tuple[str, str]] = queue.Queue()
    building = False
    exit_status = 0

    def close_window() -> None:
        if building:
            messagebox.showinfo("Build in progress", "Wait for the APK build to finish.", parent=root)
        else:
            root.destroy()

    root.protocol("WM_DELETE_WINDOW", close_window)

    def worker(selected_args: argparse.Namespace) -> None:
        try:
            result = build(selected_args, lambda line: messages.put(("log", line)))
        except Exception as error:
            messages.put(("error", str(error)))
        else:
            messages.put(("done", str(result)))

    def show_progress() -> None:
        nonlocal building, exit_status
        while True:
            try:
                kind, value = messages.get_nowait()
            except queue.Empty:
                break
            if kind == "log":
                log.configure(state="normal")
                log.insert("end", value + "\n")
                log.see("end")
                log.configure(state="disabled")
            else:
                building = False
                progress.stop()
                build_button.configure(state="normal")
                for control in controls:
                    control.configure(state="normal")
                if kind == "error":
                    exit_status = 1
                    status_value.set("Build failed. See the log and try again.")
                    log.configure(state="normal")
                    log.insert("end", "ERROR: " + value + "\n")
                    log.configure(state="disabled")
                    messagebox.showerror("Build failed", value, parent=root)
                else:
                    exit_status = 0
                    status_value.set(f"APK saved: {value}")
                    messagebox.showinfo("Build complete", f"New APK saved at:\n{value}", parent=root)
        if building:
            root.after(100, show_progress)

    def start_build() -> None:
        nonlocal building, exit_status
        if building:
            return
        try:
            selected_args = argparse.Namespace(
                original_apk=Path(original_value.get().strip()) if original_value.get().strip() else None,
                output=Path(output_value.get().strip()),
                version_code=int(version_code_value.get().strip()),
                version_name=version_name_value.get().strip(), app_id=args.app_id)
            if not output_value.get().strip():
                raise ValueError("choose a destination for the new APK")
            if selected_args.original_apk is None:
                raise ValueError("choose your original Snail Mail Android 1.00 APK")
        except ValueError as error:
            messagebox.showerror("Check build settings", str(error), parent=root)
            return
        exit_status = 0
        log.configure(state="normal")
        log.delete("1.0", "end")
        log.configure(state="disabled")
        building = True
        status_value.set("Building… this can take several minutes on a clean checkout.")
        build_button.configure(state="disabled")
        for control in controls:
            control.configure(state="disabled")
        progress.start(10)
        threading.Thread(target=worker, args=(selected_args,), daemon=True).start()
        root.after(100, show_progress)

    build_button = ttk.Button(actions, text="Build APK", command=start_build)
    build_button.pack(side="right")
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
