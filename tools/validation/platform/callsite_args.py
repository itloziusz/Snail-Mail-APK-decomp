#!/usr/bin/env python3
"""Print the statically recoverable arguments at every direct call to the
named functions (internal symbols or imports) in an ARM32 libsnailmail.so.

Uses the same constant propagation as import_census.py / gl_census.py
(armelf.ConstProp). Arguments r0-r3 (and stack words with --stack N) are
shown as constants, decoded C strings (when the constant points into
.rodata/.data), global loads, caller-argument pass-throughs, or `?`.

Examples:
  callsite_args.py RShellSoundRegister RShellMusicPlay
  callsite_args.py --binary work/apk_unzip/lib/armeabi/libsnailmail.so srand48
  callsite_args.py --json RShellSoundRegister > out.json
"""

from __future__ import annotations

import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from armelf import ArmElf, ConstProp, fmt_value, f32  # noqa: E402
from capstone import arm as A  # noqa: E402

REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
DEFAULT = os.path.join(REPO, "work/apk_unzip/lib/armeabi-v7a/libsnailmail.so")


def render(elf, v):
    d = fmt_value(elf, v)
    if d["kind"] == "const":
        out = []
        for x in (int(s, 16) for s in d["values"]):
            sec = elf.section_of(x)
            if sec in (".rodata", ".data"):
                s = elf.cstring(x)
                if s is not None and s.isprintable() and len(s) > 0:
                    out.append(repr(s))
                    continue
            if sec in (".bss", ".data", ".rodata"):
                out.append(f"&{elf.describe_addr(x)}")
                continue
            fl = f32(x)
            hint = f" (f32 {fl:g})" if (x & 0x7F800000) and abs(fl) < 1e7 and abs(fl) > 1e-7 and x > 0xFFFF else ""
            out.append(f"0x{x:x}{hint}")
        return "|".join(out)
    if d["kind"] == "global_load":
        return f"*{d['symbol']}"
    if d["kind"] == "caller_arg":
        return f"arg{d['arg_index']}"
    if d["kind"] == "import_addr":
        return f"&{d['symbol']}"
    return "?"


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("names", nargs="+", help="function names (mangled prefix, demangled-ish prefix, or import name)")
    ap.add_argument("--binary", default=DEFAULT)
    ap.add_argument("--stack", type=int, default=0, help="also show N outgoing stack words")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()

    elf = ArmElf(args.binary, os.path.basename(os.path.dirname(args.binary)))
    targets = {}
    for f in elf.funcs:
        for n in [f.name] + f.aliases:
            for want in args.names:
                # match exact, or Itanium-mangled free function _Z<len><name>
                if n == want or n.startswith(f"_Z{len(want)}{want}") or n.startswith(want + "("):
                    targets[f.addr] = n
    for stub, n in elf.plt_map.items():
        if n in args.names:
            targets[stub] = n
    if not targets:
        sys.exit(f"no symbol matches {args.names}")

    cps = {}
    rows = []
    for insn in elf.all_code_insns():
        if insn.id not in (A.ARM_INS_B, A.ARM_INS_BL):
            continue
        t = elf.branch_target(insn)
        if t not in targets:
            continue
        f = elf.func_at(insn.address)
        if f is None:
            continue
        if f.addr not in cps:
            cps[f.addr] = ConstProp(elf, f)
        st = cps[f.addr].pre_state.get(insn.address)
        vals = [render(elf, st.r.get(f"r{i}") if st else None) for i in range(4)]
        vals += [render(elf, st.stk.get(4 * i) if st else None) for i in range(args.stack)]
        rows.append({"site": f"0x{insn.address:x}", "caller": f.name, "callee": targets[t], "args": vals})
    if args.json:
        json.dump({"binary_sha256": elf.sha256, "calls": rows}, sys.stdout, indent=1)
        print()
    else:
        print(f"# {elf.path} sha256={elf.sha256}")
        for r in rows:
            print(f"{r['site']}  {r['caller']}  -> {r['callee']}({', '.join(r['args'])})")


if __name__ == "__main__":
    main()
