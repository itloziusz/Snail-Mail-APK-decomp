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


def read_allowlist(lib, with_kind=False):
    """Allowed symbol names; a line "data NAME" marks a data object."""
    out = []
    for line in (HERE / "symbols" / f"{lib}.txt").read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        kind, name = ("data", line[5:].strip()) if line.startswith("data ") else ("func", line)
        out.append((kind, name) if with_kind else name)
    return out


def jdk_include():
    for cand in [os.environ.get("JAVA_HOME", ""), "/usr/lib/jvm/java-21-openjdk-amd64"]:
        if cand and (pathlib.Path(cand) / "include" / "jni.h").exists():
            return pathlib.Path(cand) / "include"
    sys.exit("jni.h not found; set JAVA_HOME")


def build_stub(lib, target, workdir):
    src = workdir / f"stub_{lib}.c"
    src.write_text("".join(f"char {n}[16];\n" if k == "data" else f"void {n}(void) {{}}\n"
                           for k, n in read_allowlist(lib, with_kind=True)))
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
    ap.add_argument("--nowarn-prefix", action="append", default=[],
                    help="sources under this path are generated: compile with -w")
    ap.add_argument("-j", dest="jobs", type=int, default=os.cpu_count() or 1)
    ap.add_argument("-X", dest="extra", action="append", default=[], help="extra compiler flag")
    ap.add_argument("--file-flag", action="append", default=[], metavar="PATH=FLAG",
                    help="extra compiler flag only for sources at/under PATH (e.g. a variant's -D); "
                         "other sources keep their flags, so their cached objects stay valid")
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
    common += a.extra
    file_flags = []
    for ff in a.file_flag:
        path, sep, flag = ff.partition("=")
        if not sep or not path or not flag:
            sys.exit(f"--file-flag needs PATH=FLAG, got {ff!r}")
        if not os.path.exists(path):
            sys.exit(f"--file-flag path does not exist: {path}")
        file_flags.append((os.path.abspath(path), flag))
    jobs = []
    for i, src in enumerate(a.sources):
        obj = work / f"{i:03d}_{pathlib.Path(src).stem}.o"
        extra = ["-std=gnu11"] if src.endswith(".c") else ["-std=c++17", "-fno-exceptions", "-fno-rtti"]
        if any(os.path.abspath(src).startswith(os.path.abspath(p)) for p in a.nowarn_prefix):
            extra = extra + ["-w", "-g0"]  # generated: no warnings, no debug info (size/time)
        src_abs = os.path.abspath(src)
        extra = extra + [f for p, f in file_flags if src_abs == p or src_abs.startswith(p + os.sep)]
        jobs.append((["clang"] + common + extra + ["-c", src, "-o", str(obj)], obj))
    # incremental: skip objects newer than their source with identical flags
    def up_to_date(cmd, obj):
        stamp = obj.with_suffix(".cmd")
        src = cmd[-3]
        return (obj.exists() and stamp.exists() and stamp.read_text() == " ".join(map(str, cmd))
                and obj.stat().st_mtime >= os.path.getmtime(src))
    todo = [(c, o) for c, o in jobs if not up_to_date(c, o)]
    print(f"compiling {len(todo)} of {len(jobs)} sources ({a.jobs} jobs)", flush=True)
    import concurrent.futures
    with concurrent.futures.ThreadPoolExecutor(max_workers=a.jobs) as ex:
        results = list(ex.map(lambda j: subprocess.run([str(x) for x in j[0]], capture_output=True, text=True), todo))
    failed = False
    for (cmd, obj), r in zip(todo, results):
        if r.stderr:
            sys.stderr.write(r.stderr)
        if r.returncode != 0:
            print("FAILED:", " ".join(str(x) for x in cmd))
            failed = True
        else:
            obj.with_suffix(".cmd").write_text(" ".join(map(str, cmd)))
    if failed:
        sys.exit(1)
    objs = [obj for _, obj in jobs]
    stubs = [build_stub(lib, target, work) for lib in a.lib]
    run(["clang", f"--target={target}", "-shared", "-nostdlib", "-fuse-ld=lld",
         "-Wl,-soname,libsnailmail.so", f"-Wl,-z,max-page-size={PAGE}",
         "-Wl,--hash-style=both", "-Wl,-z,noexecstack", "-Wl,-z,relro", "-Wl,-z,now",
         "-Wl,--no-undefined", "-Wl,--build-id=sha1", "-o", a.out] + objs + stubs)
    verify(a.out, a.lib)


if __name__ == "__main__":
    main()
