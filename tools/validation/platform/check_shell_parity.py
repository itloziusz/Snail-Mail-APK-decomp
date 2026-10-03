#!/usr/bin/env python3
"""Check that the port's Java shell + JNI bridge keep the original JNI contract.

Compares four sources of truth:
  1. native method declarations in the ORIGINAL smali
     (work/apktool/smali/com/sandlotgames/snailmail/*.smali);
  2. native method declarations of the PORT shell, compiled with javac
     against the platform android.jar and read back with javap -s;
  3. the Java callback table the ORIGINAL native code resolves with
     GetMethodID (gJAVAFunction, v7a: 26 x {jmethodID, name*, sig*});
     every entry must exist as a public method with the same descriptor on
     the port's ADRenderer (the object passed to nativeInit);
  4. Java_* symbols exported by the original v7a .so vs JNIEXPORT functions
     defined in android/app/src/main/cpp/jni_bridge.cpp.

The port-only PortSettings.nativeSet entry is checked against its own JNI
implementation; it is intentionally absent from the original APK.

Exit status 0 when everything matches (differences that are intentional,
i.e. the removed MyOpenFeintDelegate class, are reported as INFO).

Usage: check_shell_parity.py [--android-jar PATH]
"""

from __future__ import annotations

import argparse
import glob
import os
import re
import subprocess
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from armelf import ArmElf  # noqa: E402

REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
SMALI = os.path.join(REPO, "work/apktool/smali/com/sandlotgames/snailmail")
SHELL = os.path.join(REPO, "android/app/src/main/java/com/sandlotgames/snailmail")
BRIDGE = os.path.join(REPO, "android/app/src/main/cpp/jni_bridge.cpp")
V7A = os.path.join(REPO, "work/apk_unzip/lib/armeabi-v7a/libsnailmail.so")
INTENTIONALLY_REMOVED = {"MyOpenFeintDelegate"}
PORT_ONLY_NATIVES = {
    ("PortSettings", "nativeSet", "(IIIIII)V"):
        os.path.join(REPO, "aot/port/port_android.c"),
    **{entry: os.path.join(REPO, "aot/port/port_android.c") for entry in (
        ("ADGLSurfaceView", "nativeNameEntryActive", "()Z"),
        ("ADGLSurfaceView", "nativeSafeArea", "(FFFF)V"),
        ("BackgroundArt", "nativeAsset", "(III[I)V"),
        ("BackgroundArt", "nativeContextCreated", "()V"),
        ("NameEntryDebugView", "nativeSnapshot", "()[F"),
    )},
}


def smali_natives():
    out = set()
    for p in glob.glob(os.path.join(SMALI, "*.smali")):
        cls = os.path.basename(p)[:-6]
        for line in open(p, encoding="utf-8"):
            m = re.match(r"\.method\s+(.*)\s+(\S+)\((.*)\)(\S+)$", line.strip())
            if m and " native" in " " + m.group(1):
                out.add((cls, m.group(2), f"({m.group(3)}){m.group(4)}"))
    return out


def javap_members(classes_dir):
    natives, methods = set(), {}
    for cf in glob.glob(os.path.join(classes_dir, "com/sandlotgames/snailmail/*.class")):
        cls = os.path.basename(cf)[:-6]
        if "$" in cls:
            continue
        txt = subprocess.run(["javap", "-s", "-p", cf], capture_output=True, text=True, check=True).stdout
        lines = txt.splitlines()
        for i, l in enumerate(lines):
            if "(" in l and i + 1 < len(lines) and "descriptor:" in lines[i + 1]:
                name = l.split("(")[0].split()[-1]
                desc = lines[i + 1].split("descriptor:")[1].strip()
                mods = l.strip().split()
                if "native" in mods:
                    natives.add((cls, name, desc))
                methods.setdefault(cls, {})[(name, desc)] = mods
    return natives, methods


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--android-jar", default="/usr/lib/android-sdk/platforms/android-23/android.jar")
    args = ap.parse_args()
    missing = [p for p in (SMALI, V7A, args.android_jar) if not os.path.exists(p)]
    if missing:
        # The original-derived inputs are gitignored (tools/inventory/setup_workspace.sh).
        print("SKIP: missing inputs: " + ", ".join(missing))
        return 77
    ok = True
    with tempfile.TemporaryDirectory() as td:
        srcs = glob.glob(os.path.join(SHELL, "*.java"))
        subprocess.run(["javac", "-nowarn", "-d", td, "-cp", args.android_jar, "-source", "17", "-target", "17"] + srcs,
                       check=True, capture_output=True)
        port_natives, port_methods = javap_members(td)

    orig = smali_natives()
    for cls, name, desc in sorted(orig):
        if (cls, name, desc) in port_natives:
            print(f"OK    native {cls}.{name}{desc}")
        elif cls in INTENTIONALLY_REMOVED:
            print(f"INFO  native {cls}.{name}{desc}: class intentionally removed (OpenFeint)")
        else:
            print(f"FAIL  native {cls}.{name}{desc}: missing or different in port shell")
            ok = False
    for extra in sorted(port_natives - orig):
        if extra in PORT_ONLY_NATIVES:
            source = open(PORT_ONLY_NATIVES[extra], encoding="utf-8").read()
            export = "Java_com_sandlotgames_snailmail_" + extra[0] + "_" + extra[1]
            if export in source:
                print(f"OK    port-only native {extra}: implemented in port_android.c")
                continue
        print(f"FAIL  native {extra}: unexpected or missing port JNI implementation")
        ok = False
    for expected in PORT_ONLY_NATIVES:
        if expected not in port_natives:
            print(f"FAIL  port-only native {expected}: missing or wrong descriptor")
            ok = False
    callback = port_methods.get("PortSettings", {}).get(("onChanged", "(IIIII)V"))
    if callback is None or "static" not in callback:
        print("FAIL  PortSettings.onChanged(IIIII)V callback missing")
        ok = False
    else:
        source = open(PORT_ONLY_NATIVES[("PortSettings", "nativeSet", "(IIIIII)V")], encoding="utf-8").read()
        if '"(IIIII)V"' not in source:
            print("FAIL  native callback descriptor differs from Java")
            ok = False
        else:
            print("OK    PortSettings.onChanged(IIIII)V callback")

    elf = ArmElf(V7A, "v7a")
    base = elf.sym_by_name["gJAVAFunction"]
    renderer = port_methods.get("ADRenderer", {})
    for i in range(26):
        name = elf.cstring(elf.read32(base + 12 * i + 4))
        sig = elf.cstring(elf.read32(base + 12 * i + 8))
        mods = renderer.get((name, sig))
        if mods is None:
            print(f"FAIL  callback [{i}] ADRenderer.{name}{sig} missing (GetMethodID would fail)")
            ok = False
        elif "public" not in mods or "static" in mods:
            print(f"FAIL  callback [{i}] ADRenderer.{name}{sig} must be public non-static, is {mods}")
            ok = False
        else:
            print(f"OK    callback [{i}] ADRenderer.{name}{sig}")

    orig_syms = {f.name for f in elf.funcs if f.name.startswith("Java_")}
    bridge_syms = set(re.findall(r"\b(Java_com_sandlotgames_snailmail_\w+)\s*\(", open(BRIDGE, encoding="utf-8").read()))
    for s in sorted(orig_syms | bridge_syms):
        if s in orig_syms and s in bridge_syms:
            print(f"OK    export {s}")
        else:
            print(f"FAIL  export {s}: {'missing from bridge' if s in orig_syms else 'not in original'}")
            ok = False
    print("PARITY:", "PASS" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
