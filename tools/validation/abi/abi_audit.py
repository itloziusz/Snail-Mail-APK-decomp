#!/usr/bin/env python3
"""ARM32 -> AArch64 porting audit: instruction/ABI feature census of the
original libsnailmail.so builds (analysis-only; nothing here ships).

Reports, per binary, with addresses of every hit for rare classes:
  * EABI build attributes (.ARM.attributes via readelf -A)
  * Thumb presence ($t mapping symbols, BLX-immediate, BX/BLX register)
  * VFP instruction census by mnemonic, chained multiply-accumulate
    (VMLA/VMLS/VNMLA/VNMLS), fused (VFMA/VFMS), VDIV/VSQRT, VCVT kinds,
    FPSCR access (VMRS/VMSR), VFPv3-only encodings, NEON, D16-D31 use
  * atomics / barriers (LDREX/STREX/SWP/DMB/DSB/ISB/CLREX, CP15 barriers)
  * coprocessor ops, SVC/SWI, BKPT/UDF
  * byte/halfword load signedness (LDRB vs LDRSB, UXTB vs SXTB ...)
  * register-controlled shifts (ARM semantics: amount = Rs[7:0], >=32 -> 0
    for LSL/LSR; AArch64 LSLV uses amount mod 64/32)
  * 32x32->64 multiplies, integer divide helper calls (no hardware divide)
  * soft-float helper calls (v5) and libm call census
  * TLS (PT_TLS, .tdata/.tbss, TLS relocations, CP15 c13 reads)
  * EHABI unwind tables (.ARM.exidx entries: CANTUNWIND / inline / extab)

Counts are split into `game` code and `runtime` code (libgcc division,
soft-float and EHABI unwinder routines statically linked into the .so,
identified by symbol name).

Usage: abi_audit.py [--json OUT] [BINARY ...]   (defaults: v7a and v5)
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from collections import Counter, defaultdict

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "platform"))
from armelf import ArmElf  # noqa: E402
from capstone import arm as A  # noqa: E402

REPO = os.path.abspath(os.path.join(HERE, "..", "..", ".."))
DEFAULTS = [("v7a", os.path.join(REPO, "work/apk_unzip/lib/armeabi-v7a/libsnailmail.so")),
            ("v5", os.path.join(REPO, "work/apk_unzip/lib/armeabi/libsnailmail.so"))]

RUNTIME_RE = re.compile(r"^(__aeabi_|__gnu_|_Unwind_|___Unwind_|__div|__udiv|__mod|__umod|__add|__sub|__mul|"
                        r"__fix|__float|__extend|__trunc|__cmp|__eq|__ne|__lt|__le|__gt|__ge|__unord|"
                        r"__restore_core_regs|__gnu_unwind|get_eit_entry|unwind_phase2|search_EIT_table|"
                        r"selfrel_offset31|restore_non_core_regs|__cxa_|__clz|__negdi|__ashl|__ashr|__lshr|"
                        r"next_unwind_byte|unwind_UCB_from_context|__div0|_GLOBAL__)")

VFP_CHAINED = {"vmla", "vmls", "vnmla", "vnmls"}
VFP_FUSED = {"vfma", "vfms", "vfnma", "vfnms"}
ATOMIC = {"ldrex", "ldrexb", "ldrexh", "ldrexd", "strex", "strexb", "strexh", "strexd", "swp", "swpb",
          "dmb", "dsb", "isb", "clrex"}
COPROC = {"mrc", "mcr", "mrrc", "mcrr", "cdp", "ldc", "stc", "mrc2", "mcr2", "cdp2", "ldc2", "stc2"}
TRAP = {"svc", "swi", "bkpt", "udf", "smc", "hvc"}
SIGNED_LOADS = {"ldrsb", "ldrsh"}
UNSIGNED_LOADS = {"ldrb", "ldrh"}
EXTENDS = {"sxtb", "uxtb", "sxth", "uxth", "sxtab", "uxtab", "sxtah", "uxtah"}
MUL64 = {"smull", "umull", "smlal", "umlal"}
DIV_HELPERS = {"__aeabi_idiv", "__aeabi_uidiv", "__aeabi_idivmod", "__aeabi_uidivmod", "__aeabi_ldivmod",
               "__aeabi_uldivmod", "__divsi3", "__udivsi3", "__divdi3", "__udivdi3", "__modsi3", "__umodsi3"}


def attrs(path: str) -> list[str]:
    try:
        out = subprocess.run(["readelf", "-A", path], capture_output=True, text=True, check=True).stdout
    except (OSError, subprocess.CalledProcessError) as e:
        return [f"readelf failed: {e}"]
    return [l.strip() for l in out.splitlines() if l.strip().startswith("Tag_")]


def audit(label: str, path: str) -> dict:
    elf = ArmElf(path, label)
    cnt = {"game": Counter(), "runtime": Counter()}
    sites = defaultdict(list)
    vcvt = Counter()
    helper_calls = Counter()
    helper_calls_game = Counter()
    regshift = {"game": 0, "runtime": 0}
    highd = []
    fpimm = []
    call_names = {}
    for f in elf.funcs:
        call_names[f.addr] = f.name
    for stub, n in elf.plt_map.items():
        call_names[stub] = n
    for insn in elf.all_code_insns():
        f = elf.func_at(insn.address)
        fname = f.name if f else "?"
        area = "runtime" if RUNTIME_RE.match(fname) else "game"
        m = insn.insn_name()  # capstone base name: no condition code / data-type suffix
        if m == "fmstat" or (m == "vmrs" and "apsr_nzcv" in insn.op_str.lower()):
            m = "vmrs APSR_nzcv (compare-flag transfer)"
        cnt[area][m] += 1
        rec = f"0x{insn.address:x}:{fname}"
        if m in VFP_CHAINED | VFP_FUSED | ATOMIC | COPROC | TRAP or m in ("vmrs", "vmsr", "fmxr", "fmrx"):
            sites[m].append(rec)
        if m in ("bx", "blx") and insn.operands and insn.operands[0].type == A.ARM_OP_REG and insn.operands[0].reg != A.ARM_REG_LR:
            sites[f"{m} <reg>"].append(rec)
        if m == "blx" and insn.operands and insn.operands[0].type == A.ARM_OP_IMM:
            sites["blx <imm> (to Thumb)"].append(rec)
        if m == "vcvt":
            vcvt[insn.mnemonic.split(".", 1)[1] if "." in insn.mnemonic else insn.mnemonic] += 1
            if insn.mnemonic.startswith("vcvtr"):
                sites["vcvtr (FPSCR rounding-mode conversion)"].append(rec)
            if len(insn.operands) == 3:
                sites["vcvt fixed-point (VFPv3)"].append(rec)
        if A.ARM_GRP_NEON in insn.groups:
            sites["NEON"].append(rec)
        for op in insn.operands:
            if op.type == A.ARM_OP_REG:
                n = insn.reg_name(op.reg)
                if (n.startswith("d") and n[1:].isdigit() and int(n[1:]) >= 16) or (n.startswith("q") and n[1:].isdigit()):
                    highd.append(rec)
                    break
            if op.type == A.ARM_OP_FP and m == "vmov":
                fpimm.append(rec)
            if op.type in (A.ARM_OP_REG, A.ARM_OP_MEM) and op.shift.type in (A.ARM_SFT_ASR_REG, A.ARM_SFT_LSL_REG,
                                                                             A.ARM_SFT_LSR_REG, A.ARM_SFT_ROR_REG, A.ARM_SFT_RRX_REG):
                regshift[area] += 1
                sites["register-controlled shift"].append(rec)
        if m in ("lsl", "lsr", "asr", "ror") and len(insn.operands) == 3 and insn.operands[2].type == A.ARM_OP_REG:
            regshift[area] += 1
            sites["register-controlled shift"].append(rec)
        if m == "mrc" and "c13" in insn.op_str:
            sites["mrc p15 c13 (TLS register read)"].append(rec)
        if insn.id in (A.ARM_INS_BL, A.ARM_INS_B) and insn.operands and insn.operands[0].type == A.ARM_OP_IMM:
            t = insn.operands[0].imm & 0xFFFFFFFF
            n = call_names.get(t)
            if n and (n.startswith("__aeabi_") or n in DIV_HELPERS or n.startswith("__") and n.endswith(("sf3", "df3", "si2", "sf2", "df2", "sfsi", "dfsi", "sisf", "sidf"))):
                helper_calls[n] += 1
                if area == "game":
                    helper_calls_game[n] += 1

    # TLS
    tls = {"PT_TLS": any(s["p_type"] == "PT_TLS" for s in elf.elf.iter_segments()),
           "tls_sections": [s for s in elf.sections if s.startswith((".tdata", ".tbss"))],
           "tls_relocs": sorted({t for _, (t, _) in elf.got_reloc.items() if t in (17, 18, 19, 104, 105, 106, 107, 108)})}
    # EHABI
    ex_a, ex_sz, _, _, _ = elf.sections[".ARM.exidx"]
    n = ex_sz // 8
    cant = inline = table = 0
    for i in range(n):
        w1 = elf.read32(ex_a + 8 * i + 4)
        if w1 == 1:
            cant += 1
        elif w1 & 0x80000000:
            inline += 1
        else:
            table += 1

    vfp_game = {k: v for k, v in cnt["game"].items() if k.startswith("v") or k.startswith("f") and k in ("fmxr", "fmrx", "fldmx", "fstmx")}
    out = {
        "label": label, "path": os.path.relpath(path, REPO), "sha256": elf.sha256,
        "attributes": attrs(path),
        "instructions_decoded": {"game": sum(cnt["game"].values()), "runtime": sum(cnt["runtime"].values())},
        "thumb": {"$t_mapping_symbols": elf.thumb_mapping_symbols, "odd_function_symbols": len(elf.odd_func_symbols),
                  "blx_imm": len(sites.get("blx <imm> (to Thumb)", [])), "bx_blx_reg": {k: v for k, v in sites.items() if k in ("bx <reg>", "blx <reg>")}},
        "vfp_game_by_mnemonic": dict(sorted(vfp_game.items(), key=lambda kv: -kv[1])),
        "vfp_game_total": sum(vfp_game.values()),
        "vfp_runtime_total": sum(v for k, v in cnt["runtime"].items() if k.startswith("v")),
        "vcvt_kinds": dict(vcvt),
        "vcvtr_sites": sites.get("vcvtr (FPSCR rounding-mode conversion)", []),
        "chained_mac_sites": {k: len(sites.get(k, [])) for k in sorted(VFP_CHAINED)},
        "fused_mac_sites": {k: len(sites.get(k, [])) for k in sorted(VFP_FUSED)},
        "fpscr_access": {"vmrs_to_gpr": sites.get("vmrs", []), "vmsr_writes": sites.get("vmsr", []),
                         "vmrs_apsr_flag_transfers_game": cnt["game"]["vmrs APSR_nzcv (compare-flag transfer)"]},
        "vfpv3_only": {"vmov_fp_immediate": fpimm[:20], "vmov_fp_immediate_count": len(fpimm),
                       "vcvt_fixed_point": sites.get("vcvt fixed-point (VFPv3)", [])},
        "d16_d31_or_q_register_use": highd[:20], "d16_d31_or_q_register_use_count": len(highd),
        "neon": sites.get("NEON", [])[:20], "neon_count": len(sites.get("NEON", [])),
        "atomics_barriers": {k: sites.get(k, []) for k in sorted(ATOMIC) if sites.get(k)},
        "coprocessor": {k: sites.get(k, []) for k in sorted(COPROC) if sites.get(k)},
        "svc_trap": {k: sites.get(k, []) for k in sorted(TRAP) if sites.get(k)},
        "tls_register_reads": sites.get("mrc p15 c13 (TLS register read)", []),
        "byte_half_loads_game": {k: cnt["game"][k] for k in sorted(SIGNED_LOADS | UNSIGNED_LOADS)},
        "extends_game": {k: cnt["game"][k] for k in sorted(EXTENDS)},
        "register_controlled_shifts": regshift,
        "register_controlled_shift_sites_game": [s for s in sites.get("register-controlled shift", []) if not RUNTIME_RE.match(s.split(":", 1)[1])][:40],
        "mul64_game": {k: cnt["game"][k] for k in sorted(MUL64)},
        "helper_calls_all": dict(helper_calls.most_common()),
        "helper_calls_from_game": dict(helper_calls_game.most_common()),
        "tls": tls,
        "ehabi": {".ARM.exidx_entries": n, "EXIDX_CANTUNWIND": cant, "inline_compact": inline, "extab_ref": table,
                  ".ARM.extab_size": elf.sections[".ARM.extab"][1]},
        "textrel_count": len(elf.textrel),
    }
    return out


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("binaries", nargs="*")
    ap.add_argument("--json")
    args = ap.parse_args()
    bins = [(os.path.basename(os.path.dirname(p)), p) for p in args.binaries] or DEFAULTS
    res = [audit(l, p) for l, p in bins]
    if args.json:
        with open(args.json, "w") as f:
            json.dump(res, f, indent=1)
            f.write("\n")
    for r in res:
        print(f"== {r['label']} {r['sha256']}")
        for k, v in r.items():
            if k in ("label", "sha256"):
                continue
            s = json.dumps(v)
            print(f"  {k}: {s if len(s) < 400 else s[:400] + ' ...'}")


if __name__ == "__main__":
    main()
