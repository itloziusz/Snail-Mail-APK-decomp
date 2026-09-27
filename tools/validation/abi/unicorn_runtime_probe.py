#!/usr/bin/env python3
"""Execute small leaf routines of the ORIGINAL libsnailmail.so builds under
Unicorn (ARM32 emulation, analysis only -- never part of the port) to pin
down arithmetic semantics that reconstructed AArch64 code must reproduce.

Probes (addresses from the unstripped .symtab of each build):
  v7a tVector::Dot                 chained VMLA: one or two roundings?
  v7a tColourSmall::Set(f,f,f,f)   VCVT.U32.F32 of x*255 then STRB
  v5  tColourSmall::Set(f,f,f,f)   same C++ via libgcc soft-float helpers
  v5  __aeabi_f2iz / __aeabi_f2uiz soft-float float->int edge cases
  v7a __aeabi_idiv/__aeabi_idivmod/__aeabi_uidiv  divide-by-zero, INT_MIN/-1

VFP semantics come from QEMU's implementation of the ARM ARM (via Unicorn);
this is a consistency check of the spec, not a hardware measurement.
The FPSCR is set to 0 (round-to-nearest, FZ=0, DN=0) -- see ABI_PORTING.md
for why this is the assumed Android/Linux default.

Usage: unicorn_runtime_probe.py [--v7a PATH] [--v5 PATH]
"""

from __future__ import annotations

import argparse
import math
import os
import struct
import sys
from fractions import Fraction

from elftools.elf.elffile import ELFFile
from unicorn import UC_ARCH_ARM, UC_MODE_ARM, Uc
from unicorn.arm_const import (UC_ARM_REG_C1_C0_2, UC_ARM_REG_FPEXC, UC_ARM_REG_FPSCR, UC_ARM_REG_LR,
                               UC_ARM_REG_R0, UC_ARM_REG_R1, UC_ARM_REG_R2, UC_ARM_REG_R3, UC_ARM_REG_SP)

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.abspath(os.path.join(HERE, "..", "..", ".."))
STACK = 0x7F000000
DATA = 0x60000000
RET = 0x50000000
REGS = [UC_ARM_REG_R0, UC_ARM_REG_R1, UC_ARM_REG_R2, UC_ARM_REG_R3]


def fbits(x: float) -> int:
    return struct.unpack("<I", struct.pack("<f", x))[0]


def bitsf(b: int) -> float:
    return struct.unpack("<f", struct.pack("<I", b & 0xFFFFFFFF))[0]


def f32(x) -> float:
    return struct.unpack("<f", struct.pack("<f", float(x)))[0]


class Image:
    def __init__(self, path: str):
        self.path = path
        self.syms = {}
        with open(path, "rb") as fh:
            elf = ELFFile(fh)
            for s in elf.get_section_by_name(".symtab").iter_symbols():
                if s["st_info"]["type"] == "STT_FUNC" and s["st_value"]:
                    self.syms.setdefault(s.name, s["st_value"])
            self.segs = []
            for seg in elf.iter_segments():
                if seg["p_type"] == "PT_LOAD":
                    self.segs.append((seg["p_vaddr"], seg["p_memsz"], seg.data()))

    def uc(self) -> Uc:
        uc = Uc(UC_ARCH_ARM, UC_MODE_ARM)
        for va, memsz, data in self.segs:
            lo = va & ~0xFFF
            hi = (va + memsz + 0xFFF) & ~0xFFF
            try:
                uc.mem_map(lo, hi - lo)
            except Exception:
                pass
            uc.mem_write(va, data)
        uc.mem_map(STACK - 0x10000, 0x20000)
        uc.mem_map(DATA, 0x1000)
        uc.mem_map(RET, 0x1000)
        # enable cp10/cp11 (VFP) and FPEXC.EN; FPSCR = 0
        uc.reg_write(UC_ARM_REG_C1_C0_2, uc.reg_read(UC_ARM_REG_C1_C0_2) | (0xF << 20))
        uc.reg_write(UC_ARM_REG_FPEXC, 0x40000000)
        uc.reg_write(UC_ARM_REG_FPSCR, 0)
        return uc

    def call(self, name: str, args, stack_args=(), setup=None):
        uc = self.uc()
        if setup:
            setup(uc)
        sp = STACK - 0x100
        for i, a in enumerate(stack_args):
            uc.mem_write(sp + 4 * i, struct.pack("<I", a & 0xFFFFFFFF))
        uc.reg_write(UC_ARM_REG_SP, sp)
        for r, a in zip(REGS, args):
            uc.reg_write(r, a & 0xFFFFFFFF)
        uc.reg_write(UC_ARM_REG_LR, RET)
        addr = self.syms[name]
        uc.emu_start(addr, RET, count=200000)
        return uc, [uc.reg_read(r) for r in REGS]


def exact_dot(ax, ay, az, bx, by, bz, fused):
    """binary32 model of ((ay*by) + ax*bx) + az*bz with one or two roundings per MAC."""
    if fused:
        s = f32(Fraction(ay) * Fraction(by))
        s = f32(Fraction(s) + Fraction(ax) * Fraction(bx))
        return f32(Fraction(s) + Fraction(az) * Fraction(bz))
    s = f32(ay * by)
    s = f32(s + f32(ax * bx))
    return f32(s + f32(az * bz))


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--v7a", default=os.path.join(REPO, "work/apk_unzip/lib/armeabi-v7a/libsnailmail.so"))
    ap.add_argument("--v5", default=os.path.join(REPO, "work/apk_unzip/lib/armeabi/libsnailmail.so"))
    args = ap.parse_args()
    v7a = Image(args.v7a)
    v5 = Image(args.v5)
    ok = True

    # 1. chained VMLA
    print("[1] v7a tVector::Dot (0x%x): VMLA chain, compare with 2-rounding and fused models" % v7a.syms["_ZN7tVector3DotERKS_S1_"])
    vecs = [((1.0 + 2 ** -23, 1.0, 0.0), (1.0 - 2 ** -23, -1.0, 0.0)),
            ((0.1, 0.2, 0.3), (0.3, 0.2, 0.1)),
            ((16777217.0, 3.0, 1e-8), (16777215.0, -3.0, 1e8))]
    for a, b in vecs:
        a = tuple(f32(x) for x in a)
        b = tuple(f32(x) for x in b)

        def setup(uc, a=a, b=b):
            uc.mem_write(DATA, struct.pack("<3f", *a))
            uc.mem_write(DATA + 16, struct.pack("<3f", *b))
        _, r = v7a.call("_ZN7tVector3DotERKS_S1_", [0, DATA, DATA + 16], setup=setup)
        got = bitsf(r[0])
        two = exact_dot(*a, *b, fused=False)
        fus = exact_dot(*a, *b, fused=True)
        verdict = "matches two-rounding (non-fused)" if fbits(got) == fbits(two) else "MISMATCH two-rounding"
        if fbits(two) != fbits(fus):
            verdict += f"; fused model would give {fus!r}"
        else:
            verdict += "; (fused model identical for this input)"
        print(f"    a={a} b={b} -> {got!r}  {verdict}")
        ok &= fbits(got) == fbits(two)

    # 2/3. tColourSmall::Set on both builds
    name = "_ZN12tColourSmall3SetEffff"
    inputs = [(0.5, 1.0, 0.0, 1.0), (-0.1, 1.2, 2.0, -1.0), (float("nan"), 17.0, 1e10, -1e10)]
    for img, lab in ((v7a, "v7a VFP"), (v5, "v5 soft-float")):
        print(f"[2] {lab} tColourSmall::Set(r,g,b,a) (0x{img.syms[name]:x}) -> bytes [b?,g?,r?,a?] as stored at +0..+3")
        for rgba in inputs:
            uc, _ = img.call(name, [DATA, fbits(rgba[0]), fbits(rgba[1]), fbits(rgba[2])], stack_args=[fbits(rgba[3])])
            print(f"    {rgba} -> {list(uc.mem_read(DATA, 4))}")

    # 4. v5 soft-float conversions
    print("[3] v5 libgcc soft-float conversions (original code)")
    for fn in ("__aeabi_f2iz", "__aeabi_f2uiz"):
        vals = [0.0, -1.5, 2147483520.0, 2147483648.0, -2147483904.0, 4294967040.0, 4294967296.0, float("inf"), float("-inf"), float("nan")]
        outs = []
        for x in vals:
            _, r = v5.call(fn, [fbits(x)])
            outs.append(f"{x!r}->0x{r[0]:08x}")
        print(f"    {fn} @0x{v5.syms[fn]:x}: " + ", ".join(outs))

    # 5. integer division helpers
    print("[4] v7a libgcc integer division (original code; ARMv7-A without hardware divide)")
    cases = [("__aeabi_idiv", 7, 0), ("__aeabi_idiv", -7, 0), ("__aeabi_idiv", -2 ** 31, -1), ("__aeabi_idiv", -7, 2),
             ("__aeabi_idivmod", 7, 0), ("__aeabi_idivmod", -7, 2), ("__aeabi_uidiv", 7, 0), ("__aeabi_uidivmod", 7, 0)]
    for fn, a, b in cases:
        _, r = v7a.call(fn, [a, b])
        q = r[0] - (1 << 32) if r[0] & 0x80000000 else r[0]
        extra = ""
        if fn.endswith("mod"):
            m = r[1] - (1 << 32) if r[1] & 0x80000000 else r[1]
            extra = f" rem={m}"
        print(f"    {fn}({a}, {b}) -> quot={q}{extra}")
    print("RESULT:", "consistent" if ok else "INCONSISTENT")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
