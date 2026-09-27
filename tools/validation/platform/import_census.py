#!/usr/bin/env python3
"""Census of every imported symbol of libsnailmail.so and all of its uses.

Writes analysis/native/platform_imports.json.

Method (all static, from the ELF bytes):
  1. .rel.plt R_ARM_JUMP_SLOT entries give (GOT slot, symbol). Each PLT stub is
     decoded and the GOT slot it loads is verified against the relocation, so
     stub address -> import name is established, not assumed.
  2. Every ARM instruction inside .text ranges marked `$a` by the ELF mapping
     symbols (literal pools `$d` are never decoded) is scanned for B/BL/BLX
     with an immediate target inside .plt.
  3. Function-pointer uses of PLT stubs: any 32-bit word in .text literal
     pools, .data, .data.rel.ro, .init_array or .got whose value is a PLT stub.
  4. Data imports (R_ARM_GLOB_DAT, R_ARM_ABS32): a per-function constant
     propagation (armelf.ConstProp) resolves `ldr rX, [rGOT, rOff]` style GOT
     loads; the load site and containing function are reported. ABS32
     relocations that land inside .text are reported as text relocations.

Usage:
  import_census.py [--v7a PATH] [--v5 PATH] [--out analysis/native/platform_imports.json]
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from collections import defaultdict

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from armelf import ArmElf, ConstProp, R_ARM_ABS32, R_ARM_GLOB_DAT, fmt_value, single  # noqa: E402
from capstone import arm as A  # noqa: E402

REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
DEFAULT_V7A = os.path.join(REPO, "work/apk_unzip/lib/armeabi-v7a/libsnailmail.so")
DEFAULT_V5 = os.path.join(REPO, "work/apk_unzip/lib/armeabi/libsnailmail.so")

LIBM = {"acos", "atan", "cos", "exp", "floor", "floorf", "pow", "sin", "sqrt", "tanf"}
STDIO = {"fopen", "fclose", "fdopen", "fread", "fwrite", "fseek", "ftell", "fprintf", "fputc",
         "printf", "putchar", "sprintf", "vsprintf", "__sF"}
STRMEM = {"memcpy", "memmove", "memset", "strcat", "strcpy", "strlen", "strtod", "atoi"}
STDLIB = {"malloc", "free", "qsort", "abort", "exit", "lrand48", "srand48"}
POSIX = {"chdir", "dup", "getcwd", "gettimeofday"}
SSP = {"__stack_chk_fail", "__stack_chk_guard"}
CXX = {"_Znwj", "__cxa_begin_cleanup", "__cxa_call_unexpected", "__cxa_guard_acquire",
       "__cxa_guard_release", "__cxa_type_match", "__gnu_Unwind_Find_exidx"}

# Float-vs-double prototype of each libm import (C standard / bionic headers).
LIBM_PROTO = {
    "acos": "double acos(double)", "atan": "double atan(double)", "cos": "double cos(double)",
    "exp": "double exp(double)", "floor": "double floor(double)", "floorf": "float floorf(float)",
    "pow": "double pow(double,double)", "sin": "double sin(double)", "sqrt": "double sqrt(double)",
    "tanf": "float tanf(float)",
}


# number of integer-register argument words to report per import (<=4);
# default 4. Variadic functions report the fixed args plus following words.
NARGS = {"abort": 0, "exit": 1, "malloc": 1, "free": 1, "strlen": 1, "atoi": 1, "chdir": 1, "dup": 1,
         "lrand48": 0, "srand48": 1, "fclose": 1, "ftell": 1, "putchar": 1, "strtod": 2, "strcpy": 2,
         "strcat": 2, "fopen": 2, "fdopen": 2, "getcwd": 2, "gettimeofday": 2, "fputc": 2,
         "glFinish": 0, "glLoadIdentity": 0, "glPushMatrix": 0, "glPopMatrix": 0, "_Znwj": 1,
         "__stack_chk_fail": 0, "__cxa_guard_acquire": 1, "__cxa_guard_release": 1}
# argument indices that are C strings (const char*)
STRARGS = {"fopen": (0, 1), "fdopen": (1,), "printf": (0,), "sprintf": (1,), "fprintf": (1,),
           "vsprintf": (1,), "chdir": (0,), "strcpy": (1,), "strcat": (1,), "atoi": (0,), "strtod": (0,),
           "strlen": (0,)}


def describe_arg(elf, v, name, idx):
    d = fmt_value(elf, v)
    if d["kind"] == "const" and len(d["values"]) == 1:
        x = int(d["values"][0], 16)
        sec = elf.section_of(x)
        if idx in STRARGS.get(name, ()) and sec in (".rodata", ".data"):
            s = elf.cstring(x)
            if s is not None:
                d["string"] = s
                d["string_section"] = sec
        elif sec in (".bss", ".data", ".rodata", ".text"):
            d["points_to"] = elf.describe_addr(x)
    return d


def category(name: str) -> tuple[str, str]:
    """(category, expected providing library). The provider is by-name
    convention only: the ELF has no symbol versioning, so the dynamic linker
    resolves each import against the DT_NEEDED list in order."""
    if name.startswith("gl"):
        return "gles1", "libGLESv1_CM.so"
    if name in LIBM:
        return "libm", "libm.so"
    if name in STDIO:
        return "stdio", "libc.so"
    if name in STRMEM:
        return "string", "libc.so"
    if name in STDLIB:
        return "stdlib", "libc.so"
    if name in POSIX:
        return "posix", "libc.so"
    if name in SSP:
        return "stack_protector", "libc.so"
    if name in CXX:
        return "cxx_runtime", "libstdc++.so (bionic minimal) / libgcc unwinder weak refs"
    return "other", "?"


def scan_binary(elf: ArmElf, do_dataflow: bool = True):
    res = {
        "calls": defaultdict(list),
        "fnptr": defaultdict(list),
        "coincidence": defaultdict(list),
        "data": defaultdict(list),
    }
    # 1/2: direct branches to PLT stubs
    for insn in elf.all_code_insns():
        if insn.id not in (A.ARM_INS_B, A.ARM_INS_BL, A.ARM_INS_BLX):
            continue
        t = elf.branch_target(insn)
        if t is None or not (elf.plt_start <= t < elf.plt_end):
            continue
        name = elf.plt_map.get(t)
        if name is None:
            raise RuntimeError(f"branch at 0x{insn.address:x} to non-stub PLT address 0x{t:x}")
        f = elf.func_at(insn.address)
        kind = {A.ARM_INS_B: "b (tail call)", A.ARM_INS_BL: "bl", A.ARM_INS_BLX: "blx"}[insn.id]
        if insn.cc != A.ARM_CC_AL:
            kind += f" cond={insn.mnemonic}"
        res["calls"][name].append({
            "addr": f"0x{insn.address:x}",
            "func": f.name if f else None,
            "func_addr": f"0x{f.addr:x}" if f else None,
            "kind": kind,
        })
    # 3: PLT stub addresses stored as data (function pointers)
    scan = []
    for s, e in elf.data_ranges(".text"):
        scan.append((s, e, ".text($d)"))
    for sec in (".data", ".data.rel.ro", ".init_array", ".got", ".rodata"):
        if sec in elf.sections:
            a, sz, _, _, _ = elf.sections[sec]
            scan.append((a, a + sz, sec))
    for s, e, label in scan:
        a = s
        while a + 4 <= e:
            w = elf.read32(a)
            if w is not None and elf.plt_start <= w < elf.plt_end and w in elf.plt_map:
                f = elf.func_at(a)
                rel = elf.got_reloc.get(a)
                ref = {"addr": f"0x{a:x}", "where": label, "func": f.name if f else None}
                # In this PIC object an absolute code address is only valid at
                # runtime if the word carries a dynamic relocation.
                if rel is not None:
                    ref["reloc_type"] = rel[0]
                    res["fnptr"][elf.plt_map[w]].append(ref)
                else:
                    ref["note"] = "no dynamic relocation on this word: numeric constant equal to a stub address, not a usable pointer"
                    res["coincidence"][elf.plt_map[w]].append(ref)
            a += 4
    # 4: data imports through the GOT
    data_slots = {slot: name for slot, (typ, name) in elf.got_reloc.items()
                  if typ in (R_ARM_GLOB_DAT, R_ARM_ABS32) and elf.section_of(slot) == ".got"}
    textrel_abs = [(off, name) for off, typ, name, sec in elf.textrel if typ == R_ARM_ABS32]
    for off, name in textrel_abs:
        f = elf.func_at(off)
        res["data"][name].append({"addr": f"0x{off:x}", "func": f.name if f else None,
                                  "kind": f"R_ARM_ABS32 text relocation in literal pool ({elf.section_of(off)})"})
    call_by_addr = {c["addr"]: (name, c) for name, lst in res["calls"].items() for c in lst}
    if do_dataflow:
        for f in elf.funcs:
            if f.size == 0:
                continue
            cp = ConstProp(elf, f)
            for insn in cp.insns:
                key = f"0x{insn.address:x}"
                if key in call_by_addr:
                    name, c = call_by_addr[key]
                    st = cp.pre_state.get(insn.address)
                    c["args_r0_r3"] = [describe_arg(elf, st.r.get(f"r{i}") if st else None, name, i)
                                       for i in range(NARGS.get(name, 4))]
            for insn in cp.insns:
                # PC-relative address formation of a PLT stub (function pointer)
                if insn.id == A.ARM_INS_ADD and len(insn.operands) == 3 and any(
                        o.type == A.ARM_OP_REG and o.reg == A.ARM_REG_PC for o in insn.operands[1:]):
                    st = cp.pre_state.get(insn.address)
                    if st is not None:
                        v = single(ConstProp.op_value(cp, st, insn, insn.operands[1]))
                        w = single(ConstProp.op_value(cp, st, insn, insn.operands[2]))
                        if v is not None and w is not None and ((v + w) & 0xFFFFFFFF) in elf.plt_map:
                            res["fnptr"][elf.plt_map[(v + w) & 0xFFFFFFFF]].append({
                                "addr": f"0x{insn.address:x}", "where": "pc-relative address formation",
                                "func": f.name})
                if not data_slots:
                    continue
                if insn.id != A.ARM_INS_LDR or len(insn.operands) != 2 or insn.operands[1].type != A.ARM_OP_MEM:
                    continue
                st = cp.pre_state.get(insn.address)
                if st is None:
                    continue
                addr = cp.mem_addr(st, insn, insn.operands[1])
                if addr in data_slots:
                    res["data"][data_slots[addr]].append({
                        "addr": f"0x{insn.address:x}", "func": f.name, "func_addr": f"0x{f.addr:x}",
                        "kind": f"GOT load of slot 0x{addr:x}"})
    return res


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--v7a", default=DEFAULT_V7A)
    ap.add_argument("--v5", default=DEFAULT_V5)
    ap.add_argument("--out", default=os.path.join(REPO, "analysis/native/platform_imports.json"))
    args = ap.parse_args()

    bins = {}
    for label, path in (("v7a", args.v7a), ("v5", args.v5)):
        elf = ArmElf(path, label)
        bins[label] = (elf, scan_binary(elf))

    names = sorted({n for n, _, _ in bins["v7a"][0].dyn_undef} | {n for n, _, _ in bins["v5"][0].dyn_undef})
    imports = []
    for name in names:
        cat, lib = category(name)
        entry = {"name": name, "category": cat, "expected_provider": lib}
        if name in LIBM_PROTO:
            entry["prototype"] = LIBM_PROTO[name]
        for label, (elf, res) in bins.items():
            und = {n: (b, t) for n, b, t in elf.dyn_undef}
            if name not in und:
                entry[label] = None
                continue
            slot = next((s for s, n in elf.jump_slots if n == name), None)
            stub = next((a for a, n in elf.plt_map.items() if n == name), None)
            other = [{"slot": f"0x{s:x}", "type": {R_ARM_GLOB_DAT: "R_ARM_GLOB_DAT", R_ARM_ABS32: "R_ARM_ABS32"}.get(t, t)}
                     for s, (t, n) in sorted(elf.got_reloc.items()) if n == name and t != 22]
            calls = res["calls"].get(name, [])
            entry[label] = {
                "bind": und[name][0],
                "plt_stub": f"0x{stub:x}" if stub is not None else None,
                "got_jump_slot": f"0x{slot:x}" if slot is not None else None,
                "other_relocs": other,
                "call_count": len(calls),
                "calling_functions": sorted({c["func"] for c in calls if c["func"]}),
                "call_sites": calls,
                "function_pointer_refs": res["fnptr"].get(name, []),
                "numeric_coincidences_not_pointers": res["coincidence"].get(name, []),
                "data_refs": res["data"].get(name, []),
            }
        imports.append(entry)

    summary = {}
    for label, (elf, res) in bins.items():
        by_cat = defaultdict(int)
        for e in imports:
            if e.get(label):
                by_cat[e["category"]] += e[label]["call_count"]
        uncalled = [e["name"] for e in imports if e.get(label) and e[label]["call_count"] == 0
                    and not e[label]["function_pointer_refs"] and not e[label]["data_refs"]]
        summary[label] = {
            "sha256": elf.sha256,
            "path": os.path.relpath(elf.path, REPO),
            "undefined_dynamic_symbols": len(elf.dyn_undef),
            "plt": {"start": f"0x{elf.plt_start:x}", "end": f"0x{elf.plt_end:x}", "stubs": len(elf.plt_map),
                    "layout": "PLT0 20 bytes, then 12-byte stubs (add ip,pc,#X; add ip,ip,#Y; ldr pc,[ip,#Z]!); every stub decoded and its GOT slot verified against .rel.plt"},
            "total_direct_call_sites": sum(len(v) for v in res["calls"].values()),
            "direct_call_sites_by_category": dict(sorted(by_cat.items())),
            "imports_without_any_static_reference": uncalled,
            "text_relocations": [{"offset": f"0x{o:x}", "type": t, "symbol": n, "section": s,
                                  "func": (elf.func_at(o).name if elf.func_at(o) else None)}
                                 for o, t, n, s in elf.textrel],
            "thumb_mapping_symbols": elf.thumb_mapping_symbols,
            "odd_address_function_symbols": elf.odd_func_symbols,
        }

    out = {
        "schema": "platform_imports/1",
        "generated_by": "tools/validation/platform/import_census.py",
        "method": [
            "PLT stub -> import via .rel.plt R_ARM_JUMP_SLOT; each stub decoded and GOT slot verified",
            "direct B/BL/BLX to PLT scanned over $a-mapped .text only (capstone ARM mode, word-by-word)",
            "function-pointer refs: 32-bit words equal to a PLT stub in .text $d pools, .data, .data.rel.ro, .init_array, .got, .rodata",
            "data imports (GLOB_DAT/ABS32): per-function constant propagation resolves GOT-slot loads",
        ],
        "limits": [
            "indirect calls through registers are not attributed to imports (none observed targeting PLT/GOT import slots)",
            "caller attribution uses .symtab STT_FUNC ranges; a site outside any sized symbol has func=null",
        ],
        "binaries": summary,
        "imports": imports,
    }
    os.makedirs(os.path.dirname(args.out), exist_ok=True)
    with open(args.out, "w") as f:
        json.dump(out, f, indent=1)
        f.write("\n")
    for label in bins:
        s = summary[label]
        print(f"{label}: {s['undefined_dynamic_symbols']} undefined dynsyms, {s['plt']['stubs']} PLT stubs, "
              f"{s['total_direct_call_sites']} direct call sites; uncalled={s['imports_without_any_static_reference']}")
    print(f"wrote {os.path.relpath(args.out, REPO)}")


if __name__ == "__main__":
    main()
