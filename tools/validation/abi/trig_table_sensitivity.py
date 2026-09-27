#!/usr/bin/env python3
"""How sensitive are the RMathSin/RMathCos lookup tables to libm differences?

Evidence (v7a e43bc913..., RMathInit v7a:0x19698-0x19728): for i in
0..32767 the original computes, in binary32 VFP arithmetic,
    a = (float)i * 2^-15            (vcvt.f32.s32 ; vmul by 0x38000000)
    a = a + a                       (vadd.f32)
    a = a * 3.14159274f             (vmul.f32 by 0x40490fdb)
then calls the *double* libm functions cos((double)a) and sin((double)a)
(PLT cos/sin, softfp: double in r0:r1) and stores (float)result into
RMathCos[i] (0x910b0) and RMathSin[i] (0xb10b0).

A different libm (bionic 2011 vs modern bionic vs glibc) may differ by up
to ~1 ulp in the double result. This script counts table entries whose
binary32 rounding would change under a +-1 ulp (double) perturbation of the
host libm result, i.e. entries where libm choice could matter. It uses only
the host's libm through Python's math module and does not claim that the
host result equals bionic's.

Usage: trig_table_sensitivity.py [--ulps N]
"""

import argparse
import math
import struct


def f32(x: float) -> float:
    return struct.unpack("<f", struct.pack("<f", x))[0]


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--ulps", type=int, default=1)
    args = ap.parse_args()
    pi_f = struct.unpack("<f", bytes.fromhex("db0f4940"))[0]
    scale = struct.unpack("<f", bytes.fromhex("00000038"))[0]
    assert scale == 2.0 ** -15
    sens = {"sin": [], "cos": []}
    for i in range(32768):
        a = f32(float(i) * scale)
        a = f32(a + a)
        a = f32(a * pi_f)
        for name, fn in (("sin", math.sin), ("cos", math.cos)):
            r = fn(a)
            base = f32(r)
            lo = hi = r
            changed = False
            for _ in range(args.ulps):
                lo = math.nextafter(lo, -math.inf)
                hi = math.nextafter(hi, math.inf)
                if f32(lo) != base or f32(hi) != base:
                    changed = True
            if changed:
                sens[name].append(i)
    for k, v in sens.items():
        print(f"{k}: {len(v)} of 32768 entries change binary32 value under +-{args.ulps} ulp double perturbation"
              + (f"; first indices {v[:10]}" if v else ""))


if __name__ == "__main__":
    main()
