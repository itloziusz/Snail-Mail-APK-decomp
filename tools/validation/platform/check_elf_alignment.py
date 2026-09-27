#!/usr/bin/env python3
"""16 KB page-size and 64-bit-only verification for the Snail Mail port.

Checks every given shared object, and every lib/**/*.so inside every given
APK/AAB-style zip, against the shipping rules of the arm64 port:

ELF rules (each .so):
  E1  ELFCLASS64, little endian, e_machine == EM_AARCH64 (183), ET_DYN
  E2  every PT_LOAD has p_align >= 16384 (and a power of two)
  E3  every PT_LOAD has p_offset % p_align == p_vaddr % p_align
  E4  no text relocations (DT_TEXTREL or DF_TEXTREL in DT_FLAGS)
  E5  PT_GNU_STACK present and not executable
APK rules (zip):
  A1  every lib/<abi>/*.so is under lib/arm64-v8a/ (any other ABI dir,
      e.g. armeabi, armeabi-v7a, x86, is flagged)
  A2  every lib/**/*.so entry is STORED (uncompressed), as required for
      extractNativeLibs=false / useLegacyPackaging=false loading from the APK
  A3  every such entry's file data starts at an offset that is a multiple of
      16384 (zipalign -P 16 / AGP 8.3+ behaviour)
  A4  the APK contains at least one arm64-v8a library

Exit status 0 when everything passes, 1 on any violation, 2 on usage errors.

Usage:
  check_elf_alignment.py [--page-size 16384] [--json] FILE...
  check_elf_alignment.py --self-test [--workdir DIR]

--self-test builds tiny AArch64 shared objects with aarch64-linux-gnu-gcc
(-z max-page-size=16384 -> must PASS, -z max-page-size=4096 -> must FAIL),
wraps them in stored/aligned and deflated/unaligned zips, runs the checker on
them and on the original ARM32 libsnailmail.so (must FAIL: ELF32/ARM,
4 KB alignment, TEXTREL). Artifacts go to work/scratch-platform/elfalign-selftest.
"""

from __future__ import annotations

import argparse
import io
import json
import os
import shutil
import struct
import subprocess
import sys
import tempfile
import zipfile

EM_AARCH64 = 183
EM_ARM = 40
PT_LOAD = 1
PT_DYNAMIC = 2
PT_GNU_STACK = 0x6474E551
DT_NULL = 0
DT_TEXTREL = 22
DT_FLAGS = 30
DF_TEXTREL = 0x4
PF_X = 1

REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))


class Report:
    def __init__(self):
        self.items = []  # dicts: target, rule, ok, detail

    def add(self, target, rule, ok, detail=""):
        self.items.append({"target": target, "rule": rule, "ok": bool(ok), "detail": detail})

    @property
    def ok(self):
        return all(i["ok"] for i in self.items)


def check_elf_bytes(data: bytes, name: str, rep: Report, page: int):
    if data[:4] != b"\x7fELF":
        rep.add(name, "E1", False, "not an ELF file")
        return
    ei_class, ei_data = data[4], data[5]
    if ei_data != 1:
        rep.add(name, "E1", False, "not little-endian")
        return
    is64 = ei_class == 2
    if is64:
        (e_type, e_machine, _, _, e_phoff, _, _, _, e_phentsize, e_phnum) = struct.unpack_from("<HHIQQQIHHH", data, 16)
    else:
        (e_type, e_machine, _, _, e_phoff, _, _, _, e_phentsize, e_phnum) = struct.unpack_from("<HHIIIIIHHH", data, 16)
    ok1 = is64 and e_machine == EM_AARCH64 and e_type == 3
    mname = {EM_AARCH64: "AArch64", EM_ARM: "ARM", 3: "x86", 62: "x86-64"}.get(e_machine, str(e_machine))
    rep.add(name, "E1", ok1, f"{'ELF64' if is64 else 'ELF32'} {mname} e_type={e_type}")
    loads = []
    dyn = None
    gnu_stack = None
    for i in range(e_phnum):
        off = e_phoff + i * e_phentsize
        if is64:
            p_type, p_flags, p_offset, p_vaddr, _, p_filesz, _, p_align = struct.unpack_from("<IIQQQQQQ", data, off)
        else:
            p_type, p_offset, p_vaddr, _, p_filesz, _, p_flags, p_align = struct.unpack_from("<IIIIIIII", data, off)
        if p_type == PT_LOAD:
            loads.append((p_offset, p_vaddr, p_align))
        elif p_type == PT_DYNAMIC:
            dyn = (p_offset, p_filesz)
        elif p_type == PT_GNU_STACK:
            gnu_stack = p_flags
    if not loads:
        rep.add(name, "E2", False, "no PT_LOAD")
    for idx, (p_offset, p_vaddr, p_align) in enumerate(loads):
        pow2 = p_align and (p_align & (p_align - 1)) == 0
        rep.add(name, "E2", pow2 and p_align >= page, f"PT_LOAD[{idx}] p_align=0x{p_align:x} (need >= 0x{page:x})")
        congruent = p_align == 0 or (p_offset % p_align) == (p_vaddr % p_align)
        rep.add(name, "E3", congruent, f"PT_LOAD[{idx}] p_offset=0x{p_offset:x} p_vaddr=0x{p_vaddr:x}")
    textrel = False
    if dyn:
        off, size = dyn
        ent = 16 if is64 else 8
        fmt = "<qQ" if is64 else "<iI"
        for j in range(size // ent):
            tag, val = struct.unpack_from(fmt, data, off + j * ent)
            if tag == DT_NULL:
                break
            if tag == DT_TEXTREL or (tag == DT_FLAGS and val & DF_TEXTREL):
                textrel = True
    rep.add(name, "E4", not textrel, "DT_TEXTREL present" if textrel else "no text relocations")
    if gnu_stack is None:
        rep.add(name, "E5", False, "no PT_GNU_STACK (stack executable by default)")
    else:
        rep.add(name, "E5", not (gnu_stack & PF_X), f"PT_GNU_STACK flags=0x{gnu_stack:x}")


def zip_data_offset(fp, info: zipfile.ZipInfo) -> int:
    fp.seek(info.header_offset)
    hdr = fp.read(30)
    sig, _, _, _, _, _, _, _, _, nlen, xlen = struct.unpack("<IHHHHHIIIHH", hdr)
    if sig != 0x04034B50:
        raise ValueError(f"bad local header for {info.filename}")
    return info.header_offset + 30 + nlen + xlen


def check_apk(path: str, rep: Report, page: int):
    with open(path, "rb") as fp, zipfile.ZipFile(fp) as z:
        libs = [i for i in z.infolist() if i.filename.startswith("lib/") and i.filename.endswith(".so")]
        arm64 = [i for i in libs if i.filename.startswith("lib/arm64-v8a/")]
        rep.add(path, "A4", bool(arm64), f"{len(arm64)} arm64-v8a libraries, {len(libs)} total")
        for info in libs:
            tgt = f"{path}!{info.filename}"
            parts = info.filename.split("/")
            abi = parts[1] if len(parts) > 2 else "?"
            rep.add(tgt, "A1", abi == "arm64-v8a", f"ABI directory '{abi}'")
            stored = info.compress_type == zipfile.ZIP_STORED
            rep.add(tgt, "A2", stored, "stored" if stored else f"compressed (method {info.compress_type})")
            doff = zip_data_offset(fp, info)
            rep.add(tgt, "A3", stored and doff % page == 0, f"data offset 0x{doff:x} (mod 0x{page:x} = 0x{doff % page:x})")
            check_elf_bytes(z.read(info), tgt, rep, page)


def check_path(path: str, rep: Report, page: int):
    with open(path, "rb") as f:
        head = f.read(4)
    if head == b"\x7fELF":
        with open(path, "rb") as f:
            check_elf_bytes(f.read(), path, rep, page)
    elif head[:2] == b"PK":
        check_apk(path, rep, page)
    else:
        rep.add(path, "input", False, "neither ELF nor zip")


def print_report(rep: Report, as_json: bool):
    if as_json:
        json.dump({"ok": rep.ok, "results": rep.items}, sys.stdout, indent=1)
        print()
        return
    for i in rep.items:
        print(f"{'PASS' if i['ok'] else 'FAIL'}  {i['rule']:3s} {i['target']}: {i['detail']}")
    print("OVERALL:", "PASS" if rep.ok else "FAIL")


# ---------------------------------------------------------------- self-test
def write_aligned_zip(dst: str, members: list[tuple[str, bytes]], stored_align: int | None):
    """Write a zip; .so members are STORED and padded (via the local extra
    field) so their data starts on `stored_align`, or DEFLATED when None."""
    with open(dst, "wb") as fp:
        z = zipfile.ZipFile(fp, "w")
        for name, data in members:
            info = zipfile.ZipInfo(name, date_time=(2026, 1, 1, 0, 0, 0))
            if name.endswith(".so") and stored_align:
                info.compress_type = zipfile.ZIP_STORED
                hdr_end = fp.tell() + 30 + len(name.encode())
                pad = (-hdr_end) % stored_align
                if 0 < pad < 4:
                    pad += stored_align
                if pad:
                    # 0xD935 is the extra-field id used by zipalign for alignment padding
                    info.extra = struct.pack("<HH", 0xD935, pad - 4) + b"\0" * (pad - 4)
            else:
                info.compress_type = zipfile.ZIP_DEFLATED
            z.writestr(info, data)
        z.close()


def self_test(workdir: str) -> int:
    os.makedirs(workdir, exist_ok=True)
    cc = shutil.which("aarch64-linux-gnu-gcc")
    if not cc:
        print("self-test needs aarch64-linux-gnu-gcc", file=sys.stderr)
        return 2
    src = os.path.join(workdir, "t.c")
    with open(src, "w") as f:
        f.write("int sm_selftest_value(void) { return 42; }\n")
    so16 = os.path.join(workdir, "lib16k.so")
    so4 = os.path.join(workdir, "lib4k.so")
    cmds = [[cc, "-shared", "-fPIC", "-O2", "-Wl,-z,max-page-size=16384", "-Wl,-z,noexecstack", "-o", so16, src],
            [cc, "-shared", "-fPIC", "-O2", "-Wl,-z,max-page-size=4096", "-Wl,-z,noexecstack", "-o", so4, src]]
    for c in cmds:
        print("$", " ".join(c))
        subprocess.run(c, check=True)
    good_apk = os.path.join(workdir, "good.apk")
    bad_apk = os.path.join(workdir, "bad.apk")
    with open(so16, "rb") as f:
        d16 = f.read()
    with open(so4, "rb") as f:
        d4 = f.read()
    write_aligned_zip(good_apk, [("AndroidManifest.xml", b"<manifest/>"), ("lib/arm64-v8a/libsnailmail.so", d16)], 16384)
    write_aligned_zip(bad_apk, [("AndroidManifest.xml", b"<manifest/>"), ("lib/arm64-v8a/libsnailmail.so", d4),
                                ("lib/armeabi-v7a/libsnailmail.so", d16)], None)
    expectations = [(so16, True), (so4, False), (good_apk, True), (bad_apk, False)]
    orig = os.path.join(REPO, "work/apk_unzip/lib/armeabi-v7a/libsnailmail.so")
    if os.path.exists(orig):
        expectations.append((orig, False))
    failures = 0
    for path, want in expectations:
        rep = Report()
        check_path(path, rep, 16384)
        got = rep.ok
        status = "ok" if got == want else "UNEXPECTED"
        failures += got != want
        print(f"[self-test] {os.path.relpath(path, REPO)}: expected {'PASS' if want else 'FAIL'}, got {'PASS' if got else 'FAIL'} -> {status}")
        for i in rep.items:
            if not i["ok"]:
                print(f"     FAIL {i['rule']} {i['detail']}")
    print("SELF-TEST:", "PASS" if failures == 0 else "FAIL")
    return 0 if failures == 0 else 1


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("files", nargs="*")
    ap.add_argument("--page-size", type=int, default=16384)
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--self-test", action="store_true")
    ap.add_argument("--workdir", default=os.path.join(REPO, "work/scratch-platform/elfalign-selftest"))
    args = ap.parse_args()
    if args.self_test:
        return self_test(args.workdir)
    if not args.files:
        ap.print_usage()
        return 2
    rep = Report()
    for p in args.files:
        check_path(p, rep, args.page_size)
    print_report(rep, args.json)
    return 0 if rep.ok else 1


if __name__ == "__main__":
    sys.exit(main())
