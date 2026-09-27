#!/usr/bin/env python3
"""Build an arm64-v8a Android shared library WITHOUT the Android NDK.

Why this exists: the pinned NDK cannot be downloaded on the analysis machine
(dl.google.com is blocked by the network policy). This driver uses the host's
clang/lld (which support the aarch64-linux-android target) plus:

  * link-time stub libraries (libc.so, libm.so, liblog.so, ...) generated from
    the explicit allowlists in symbols/*.txt — the same technique the NDK uses
    for its own stub libraries; at runtime the device's real bionic libraries
    are loaded by soname;
  * the minimal declarations in include/ (see include/README.md);
  * the JDK's jni.h (JNI is a standardised, layout-stable interface).

After linking it VERIFIES the output: ELF64 AArch64, every PT_LOAD aligned to
>= 16 KiB, no TEXTREL, DT_NEEDED limited to the stub sonames, and every
undefined symbol present in an allowlist. Any violation fails the build.

This is a stop-gap: switch to the pinned NDK (docs/BUILDING.md) when available.
"""
import argparse
import os
import pathlib
import subprocess
import sys

from elftools.elf.elffile import ELFFile
from elftools.elf.dynamic import DynamicSection

HERE = pathlib.Path(__file__).resolve().parent
PAGE = 16384


def run(cmd):
    print("+", " ".join(str(c) for c in cmd), flush=True)
    subprocess.run([str(c) for c in cmd], check=True)


def read_allowlist(lib):
    names = []
    for line in (HERE / "symbols" / f"{lib}.txt").read_text().splitlines():
        line = line.strip()
        if line and not line.startswith("#"):
            names.append(line)
    return names


def jdk_include():
    for cand in [os.environ.get("JAVA_HOME", ""), "/usr/lib/jvm/java-21-openjdk-amd64"]:
        if cand and (pathlib.Path(cand) / "include" / "jni.h").exists():
            return pathlib.Path(cand) / "include"
    sys.exit("jni.h not found; set JAVA_HOME")


def build_stub(lib, target, workdir):
    src = workdir / f"stub_{lib}.c"
    src.write_text("".join(f"void {n}(void) {{}}\n" for n in read_allowlist(lib)))
    out = workdir / f"{lib}.so"
    # Stub bodies are never executed (the device's real library is loaded by
    # soname), so their C signatures are irrelevant: silence builtin checks.
    run(["clang", f"--target={target}", "-shared", "-nostdlib", "-fuse-ld=lld", "-w",
         "-fno-builtin", f"-Wl,-soname,{lib}.so", "-o", out, src])
    return out


def verify(so_path, libs):
    allowed = set()
    for lib in libs:
        allowed.update(read_allowlist(lib))
    errors = []
    with open(so_path, "rb") as f:
        elf = ELFFile(f)
        if elf.elfclass != 64 or elf["e_machine"] != "EM_AARCH64":
            errors.append(f"not ELF64 AArch64: class={elf.elfclass} machine={elf['e_machine']}")
        for seg in elf.iter_segments():
            if seg["p_type"] == "PT_LOAD":
                if seg["p_align"] < PAGE:
                    errors.append(f"PT_LOAD p_align {seg['p_align']:#x} < {PAGE:#x}")
                if seg["p_offset"] % seg["p_align"] != seg["p_vaddr"] % seg["p_align"]:
                    errors.append("PT_LOAD offset/vaddr congruence violated")
        needed, undefined, exported = [], [], []
        for sec in elf.iter_sections():
            if isinstance(sec, DynamicSection):
                for tag in sec.iter_tags():
                    if tag.entry.d_tag == "DT_NEEDED":
                        needed.append(tag.needed)
                    if tag.entry.d_tag == "DT_TEXTREL":
                        errors.append("DT_TEXTREL present")
        dynsym = elf.get_section_by_name(".dynsym")
        for sym in dynsym.iter_symbols():
            if not sym.name:
                continue
            if sym["st_shndx"] == "SHN_UNDEF":
                undefined.append(sym.name)
            elif sym["st_info"]["bind"] in ("STB_GLOBAL", "STB_WEAK"):
                exported.append(sym.name)
        for n in needed:
            if n not in {f"{lib}.so" for lib in libs}:
                errors.append(f"unexpected DT_NEEDED {n}")
        for u in undefined:
            if u not in allowed:
                errors.append(f"undefined symbol not in allowlist: {u}")
    print(f"verify {so_path}: NEEDED={needed}")
    print(f"  imports ({len(undefined)}): {' '.join(sorted(undefined))}")
    print(f"  JNI exports: {' '.join(sorted(e for e in exported if e.startswith('Java_')))}")
    if errors:
        for e in errors:
            print("  ERROR:", e)
        sys.exit(1)
    print("  OK: ELF64 AArch64, PT_LOAD align >= 16 KiB, no TEXTREL, imports allowlisted")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", required=True)
    ap.add_argument("--api", default="24")
    ap.add_argument("--workdir", required=True)
    ap.add_argument("--lib", action="append", default=[], help="stub lib to link, e.g. libc")
    ap.add_argument("-I", dest="incs", action="append", default=[])
    ap.add_argument("-D", dest="defs", action="append", default=[])
    ap.add_argument("sources", nargs="+")
    a = ap.parse_args()

    target = f"aarch64-linux-android{a.api}"
    work = pathlib.Path(a.workdir)
    work.mkdir(parents=True, exist_ok=True)
    res = subprocess.run(["clang", "-print-resource-dir"], capture_output=True, text=True,
                         check=True).stdout.strip()
    jdk = jdk_include()
    common = [f"--target={target}", "-fPIC", "-O2", "-g", "-nostdinc",
              "-isystem", f"{res}/include", "-isystem", HERE / "include",
              "-isystem", jdk, "-isystem", jdk / "linux",
              "-fno-stack-protector", "-mno-outline-atomics", "-ffp-contract=off", "-funsigned-char", "-fwrapv",
              "-fno-strict-aliasing", "-fvisibility=hidden",
              "-Wall", "-Wextra", "-Werror=implicit-function-declaration"]
    common += [f"-I{i}" for i in a.incs] + [f"-D{d}" for d in a.defs]
    objs = []
    for i, src in enumerate(a.sources):
        obj = work / f"{i:02d}_{pathlib.Path(src).stem}.o"
        extra = ["-std=c11"] if src.endswith(".c") else ["-std=c++17", "-fno-exceptions", "-fno-rtti"]
        run(["clang"] + common + extra + ["-c", src, "-o", obj])
        objs.append(obj)
    stubs = [build_stub(lib, target, work) for lib in a.lib]
    run(["clang", f"--target={target}", "-shared", "-nostdlib", "-fuse-ld=lld",
         "-Wl,-soname,libsnailmail.so", f"-Wl,-z,max-page-size={PAGE}",
         "-Wl,--hash-style=both", "-Wl,-z,noexecstack", "-Wl,-z,relro", "-Wl,-z,now",
         "-Wl,--no-undefined", "-Wl,--build-id=sha1", "-o", a.out] + objs + stubs)
    verify(a.out, a.lib)


if __name__ == "__main__":
    main()
