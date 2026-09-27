#!/usr/bin/env python3
"""Deterministic ELF audit of one Snail Mail native library.

Usage:
    tools/decompilation/elf_audit.py v7a > analysis/native/elf_audit.v7a.json
    tools/decompilation/elf_audit.py v5  > analysis/native/elf_audit.v5.json
    (or: elf_audit.py --all   writes both files)

Covers: header + e_flags, .ARM.attributes (parsed from raw bytes), sections,
segments (incl. p_align), dynamic entries, NEEDED/SONAME, exports/imports
(+PLT stub per import), relocation census (by type and by target section),
TEXTREL evidence, INIT_ARRAY/FINI entries resolved through relocations,
TLS presence, EXIDX/extab census (+ personality routines), .comment strings,
mapping-symbol census and instruction-set evidence (capstone over $a/$t
regions only).
"""
from __future__ import annotations

import json
import struct
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import armdis  # noqa: E402
import smelf  # noqa: E402
from smelf import hx  # noqa: E402

import capstone  # noqa: E402
import elftools  # noqa: E402

# ---------------------------------------------------------------- ARM attributes
TAG_NAMES = {
    4: "Tag_CPU_raw_name", 5: "Tag_CPU_name", 6: "Tag_CPU_arch", 7: "Tag_CPU_arch_profile",
    8: "Tag_ARM_ISA_use", 9: "Tag_THUMB_ISA_use", 10: "Tag_FP_arch", 11: "Tag_WMMX_arch",
    12: "Tag_Advanced_SIMD_arch", 13: "Tag_PCS_config", 14: "Tag_ABI_PCS_R9_use",
    15: "Tag_ABI_PCS_RW_data", 16: "Tag_ABI_PCS_RO_data", 17: "Tag_ABI_PCS_GOT_use",
    18: "Tag_ABI_PCS_wchar_t", 19: "Tag_ABI_FP_rounding", 20: "Tag_ABI_FP_denormal",
    21: "Tag_ABI_FP_exceptions", 22: "Tag_ABI_FP_user_exceptions", 23: "Tag_ABI_FP_number_model",
    24: "Tag_ABI_align_needed", 25: "Tag_ABI_align_preserved", 26: "Tag_ABI_enum_size",
    27: "Tag_ABI_HardFP_use", 28: "Tag_ABI_VFP_args", 29: "Tag_ABI_WMMX_args",
    30: "Tag_ABI_optimization_goals", 31: "Tag_ABI_FP_optimization_goals", 32: "Tag_compatibility",
    34: "Tag_CPU_unaligned_access", 36: "Tag_FP_HP_extension", 38: "Tag_ABI_FP_16bit_format",
    42: "Tag_MPextension_use", 44: "Tag_DIV_use", 64: "Tag_nodefaults", 65: "Tag_also_compatible_with",
    66: "Tag_T2EE_use", 67: "Tag_conformance", 68: "Tag_Virtualization_use",
}
CPU_ARCH = {0: "Pre-v4", 1: "v4", 2: "v4T", 3: "v5T", 4: "v5TE", 5: "v5TEJ", 6: "v6", 7: "v6KZ",
            8: "v6T2", 9: "v6K", 10: "v7", 11: "v6-M", 12: "v6S-M", 13: "v7E-M", 14: "v8"}
FP_ARCH = {0: "none", 1: "VFPv1", 2: "VFPv2", 3: "VFPv3", 4: "VFPv3-D16", 5: "VFPv4", 6: "VFPv4-D16"}
HARDFP = {0: "tag_fp_arch", 1: "SP only", 2: "DP only", 3: "deprecated (SP and DP)"}
VFP_ARGS = {0: "AAPCS base (core registers)", 1: "VFP registers", 2: "custom", 3: "compatible"}
STRING_TAGS = {4, 5, 67}


def _uleb(buf, pos):
    result = shift = 0
    while True:
        byte = buf[pos]
        pos += 1
        result |= (byte & 0x7F) << shift
        shift += 7
        if not byte & 0x80:
            return result, pos


def parse_arm_attributes(raw: bytes) -> dict:
    out = {"format_version": chr(raw[0]) if raw else None, "vendors": []}
    pos = 1
    while pos < len(raw):
        sec_len = struct.unpack_from("<I", raw, pos)[0]
        end = pos + sec_len
        name_end = raw.index(b"\x00", pos + 4)
        vendor = raw[pos + 4:name_end].decode()
        p = name_end + 1
        subs = []
        while p < end:
            tag, p2 = _uleb(raw, p)
            size = struct.unpack_from("<I", raw, p2)[0]
            sub_end = p + size
            q = p2 + 4
            attrs = []
            if tag == 1:  # Tag_File
                while q < sub_end:
                    at, q = _uleb(raw, q)
                    name = TAG_NAMES.get(at, f"Tag_{at}")
                    if at in STRING_TAGS or (at > 32 and at % 2 == 1):
                        z = raw.index(b"\x00", q)
                        val = raw[q:z].decode("latin-1")
                        q = z + 1
                        attrs.append({"tag": at, "name": name, "value": val})
                    elif at == 32:
                        v, q = _uleb(raw, q)
                        z = raw.index(b"\x00", q)
                        attrs.append({"tag": at, "name": name, "value": [v, raw[q:z].decode("latin-1")]})
                        q = z + 1
                    else:
                        v, q = _uleb(raw, q)
                        rec = {"tag": at, "name": name, "value": v}
                        dec = {6: CPU_ARCH, 10: FP_ARCH, 27: HARDFP, 28: VFP_ARGS}.get(at)
                        if dec is not None:
                            rec["decoded"] = dec.get(v, str(v))
                        if at == 7:
                            rec["decoded"] = chr(v) if v else "none"
                        attrs.append(rec)
            subs.append({"scope_tag": tag, "attributes": attrs})
            p = sub_end
        out["vendors"].append({"vendor": vendor, "subsections": subs})
        pos = end
    # interpretation helpers
    flat = {a["name"]: a for v in out["vendors"] for s in v["subsections"] for a in s["attributes"]}
    fp = flat.get("Tag_FP_arch", {}).get("value", 0)
    vfp_args = flat.get("Tag_ABI_VFP_args", {}).get("value", 0)
    out["float_abi_inference"] = (
        "soft-float (no FP arch tag): floats in core registers, soft-float helpers" if not fp else
        ("hard-float (VFP register arguments)" if vfp_args == 1 else
         "softfp: VFP instructions allowed, but Tag_ABI_VFP_args absent/0 => float arguments passed in core registers (AAPCS base)"))
    return out


# ---------------------------------------------------------------- helpers
def sym_at_exact(b, addr, types=("STT_FUNC",)):
    names = sorted({s.name for s in b.symtab if s.value == addr and s.type in types and b.defined(s) and s.name})
    return names


def e_flags_decode(flags):
    d = {"raw": hx(flags), "eabi_version": (flags >> 24) & 0xFF}
    bits = []
    if flags & 0x200:
        bits.append("EF_ARM_ABI_FLOAT_SOFT")
    if flags & 0x400:
        bits.append("EF_ARM_ABI_FLOAT_HARD")
    if flags & 0x00800000:
        bits.append("EF_ARM_BE8")
    if flags & 0x00400000:
        bits.append("EF_ARM_LE8")
    d["flag_bits"] = bits
    d["note"] = ("float-ABI bits not set (common for GCC 4.4 EABI5 output); use .ARM.attributes"
                 if not flags & 0x600 else "")
    return d


def audit(name: str) -> dict:
    b = smelf.Binary(name)
    e = b.elf
    hdr = e.header
    out = {
        "schema": "snailmail-elf-audit/1",
        "binary": name,
        "apk_path": b.meta["apk_path"],
        "binary_sha256": b.sha256,
        "file_size": len(b.data),
        "tools": {"pyelftools": elftools.__version__, "capstone": ".".join(map(str, capstone.cs_version()[:2])) + f" (python {capstone.__version__})",
                  "demangler": smelf.demangle_tool()},
    }
    out["header"] = {
        "class": hdr["e_ident"]["EI_CLASS"], "data": hdr["e_ident"]["EI_DATA"],
        "osabi": hdr["e_ident"]["EI_OSABI"], "type": hdr["e_type"], "machine": hdr["e_machine"],
        "entry": hx(hdr["e_entry"]),
        "entry_symbols": sym_at_exact(b, hdr["e_entry"]),
        "e_flags": e_flags_decode(hdr["e_flags"]),
        "phnum": hdr["e_phnum"], "shnum": hdr["e_shnum"],
    }
    attr_sec = b.sec_by_name.get(".ARM.attributes")
    out["arm_attributes"] = parse_arm_attributes(b.data[attr_sec["offset"]:attr_sec["offset"] + attr_sec["size"]]) if attr_sec else None
    out["sections"] = [{
        "index": s["index"], "name": s["name"], "type": s["type"], "addr": hx(s["addr"]),
        "offset": hx(s["offset"]), "size": s["size"], "size_hex": hx(s["size"]),
        "flags": hx(s["flags"]), "align": s["align"], "entsize": s["entsize"],
        "link": s["link"], "info": s["info"],
    } for s in b.sections]
    out["segments"] = [{
        "type": s["type"], "offset": hx(s["offset"]), "vaddr": hx(s["vaddr"]), "paddr": hx(s["paddr"]),
        "filesz": hx(s["filesz"]), "memsz": hx(s["memsz"]),
        "flags": "".join(c for c, bit in (("R", 4), ("W", 2), ("X", 1)) if s["flags"] & bit),
        "align": hx(s["align"]),
        "sections": sorted([x["name"] for x in b.sections if x["name"] and x["flags"] & 2 and x["size"]
                            and s["vaddr"] <= x["addr"] and x["addr"] + x["size"] <= s["vaddr"] + s["memsz"]],
                           key=lambda n: b.sec_by_name[n]["addr"]),
    } for s in b.segments]
    loads = b.loads
    out["load_notes"] = {
        "first_load_vaddr": hx(loads[0]["vaddr"]),
        "p_align_values": sorted({hx(s["align"]) for s in loads}),
        "rw_segment_offset_vs_vaddr_delta": hx(loads[1]["vaddr"] - loads[1]["offset"]) if len(loads) > 1 else None,
        "note": "p_align 0x1000 (4 KiB pages). A 16 KiB-page arm64 port rebuilds from source, so this only matters for the original .so.",
    }
    # dynamic
    dyn = []
    needed = []
    soname = None
    dynsec = e.get_section_by_name(".dynamic")
    for t in dynsec.iter_tags():
        tag = t.entry.d_tag
        val = t.entry.d_val
        rec = {"tag": tag if isinstance(tag, str) else hx(tag), "value": hx(val)}
        if tag == "DT_NEEDED":
            rec["name"] = t.needed
            needed.append(t.needed)
        if tag == "DT_SONAME":
            rec["name"] = t.soname
            soname = t.soname
        dyn.append(rec)
    out["dynamic"] = dyn
    out["needed"] = needed
    out["soname"] = soname
    dyn_tags = {d["tag"] for d in dyn}
    out["dynamic_flags"] = {
        "DT_TEXTREL": "DT_TEXTREL" in dyn_tags, "DT_SYMBOLIC": "DT_SYMBOLIC" in dyn_tags,
        "DT_INIT": "DT_INIT" in dyn_tags, "DT_FINI": "DT_FINI" in dyn_tags,
        "DT_INIT_ARRAY": "DT_INIT_ARRAY" in dyn_tags, "DT_FINI_ARRAY": "DT_FINI_ARRAY" in dyn_tags,
        "DT_PREINIT_ARRAY": "DT_PREINIT_ARRAY" in dyn_tags, "DT_FLAGS": "DT_FLAGS" in dyn_tags,
    }
    # exports / imports
    exports = []
    for s in b.dynsym:
        if s.name and b.defined(s):
            exports.append({"name": s.name, "addr": hx(s.value), "size": s.size, "type": s.type,
                            "bind": s.bind, "section": s.section})
    exports.sort(key=lambda x: (int(x["addr"], 16), x["name"]))
    plt_by_name = {p.import_name: p for p in b.plt if p.import_name}
    got_imports = {r.sym_name: r for r in b.relocs if r.type in (2, 21) and r.sym_shndx == "SHN_UNDEF"}
    imports = []
    for s in b.imported_syms():
        p = plt_by_name.get(s.name)
        g = got_imports.get(s.name)
        imports.append({"name": s.name, "type": s.type, "bind": s.bind,
                        "plt_stub": hx(p.addr) if p else None, "got_slot": hx(p.got_slot) if p else (hx(g.offset) if g else None),
                        "reloc": "R_ARM_JUMP_SLOT" if p else (g.type_name if g else None)})
    imports.sort(key=lambda x: x["name"])
    jni = [x for x in exports if x["name"].startswith("Java_")]
    out["exports"] = {
        "count_defined_dynsym": len(exports),
        "by_type": dict(sorted(Counter(x["type"] for x in exports).items())),
        "jni_exports": [{"name": x["name"], "addr": x["addr"], "size": x["size"]} for x in jni],
        "has_JNI_OnLoad": any(x["name"] == "JNI_OnLoad" for x in exports),
        "list": exports,
    }
    out["imports"] = {
        "count": len(imports),
        "by_type": dict(sorted(Counter(x["type"] for x in imports).items())),
        "weak": sorted(x["name"] for x in imports if x["bind"] == "STB_WEAK"),
        "dynamic_loading_imports": sorted(x["name"] for x in imports if x["name"] in ("dlopen", "dlsym", "dlclose", "dlerror", "dladdr")),
        "list": imports,
    }
    out["plt"] = {"stub_count": len(b.plt), "header_bytes": 20, "stub_bytes": 12,
                  "stubs_without_jump_slot": [hx(p.addr) for p in b.plt if not p.import_name]}
    # relocations
    by_type = Counter(r.type_name for r in b.relocs)
    by_type_sec = Counter((r.type_name, b.section_of(r.offset) or "?") for r in b.relocs)
    ro_ranges = [(s["vaddr"], s["vaddr"] + s["memsz"]) for s in loads if not s["flags"] & 2]
    textrels = [r for r in b.relocs if any(lo <= r.offset < hi for lo, hi in ro_ranges)]
    out["relocations"] = {
        "count": len(b.relocs),
        "by_type": dict(sorted(by_type.items())),
        "by_type_and_target_section": [{"type": t, "section": s, "count": c} for (t, s), c in sorted(by_type_sec.items())],
        "RELCOUNT": next((int(d["value"], 16) for d in dyn if d["tag"] == "DT_RELCOUNT"), None),
        "non_relative_non_jumpslot": [{"offset": hx(r.offset), "type": r.type_name, "symbol": r.sym_name,
                                       "symbol_defined": r.sym_shndx not in ("SHN_UNDEF", None),
                                       "section": b.section_of(r.offset), "stored_addend": hx(b.u32(r.offset))}
                                      for r in b.relocs if r.type not in (22, 23)],
        "textrel": {
            "DT_TEXTREL": "DT_TEXTREL" in dyn_tags,
            "relocations_in_read_only_segments": len(textrels),
            "list": [{"offset": hx(r.offset), "type": r.type_name, "symbol": r.sym_name,
                      "section": b.section_of(r.offset)} for r in sorted(textrels, key=lambda r: r.offset)],
        },
        "tls_relocations": sum(1 for r in b.relocs if r.type in (17, 18, 19)),
    }
    # INIT_ARRAY / FINI
    def array_entries(secname):
        s = b.sec_by_name.get(secname)
        if not s:
            return None
        ents = []
        for k in range(s["size"] // 4):
            va = s["addr"] + 4 * k
            rw = b.resolve_word(va)
            raw = rw["raw"]
            val = rw["value"]
            sentinel = raw in (0, 0xFFFFFFFF) and rw["kind"] == "plain"
            ents.append({
                "index": k, "slot": hx(va), "raw_word": hx(raw), "reloc": rw["kind"],
                "resolved": hx(val) if val is not None else None,
                "sentinel": ("0xffffffff (-1)" if raw == 0xFFFFFFFF else "0") if sentinel else None,
                "symbols": sym_at_exact(b, val) if val is not None and not sentinel else [],
                "demangled": list(smelf.demangle_many(sym_at_exact(b, val)).values()) if val is not None and not sentinel else [],
            })
        return {"section": secname, "addr": hx(s["addr"]), "size": s["size"], "entries": ents}
    out["init_array"] = array_entries(".init_array")
    out["fini_array"] = array_entries(".fini_array")
    out["preinit_array"] = array_entries(".preinit_array")
    # TLS
    out["tls"] = {
        "PT_TLS": any(s["type"] == "PT_TLS" for s in b.segments),
        "SHF_TLS_sections": [s["name"] for s in b.sections if s["flags"] & 0x400],
        "STT_TLS_symbols": sum(1 for s in b.symtab + b.dynsym if s.type == "STT_TLS"),
        "tls_relocations": out["relocations"]["tls_relocations"],
    }
    # EXIDX
    kinds = Counter(x.kind for x in b.exidx)
    pers = Counter()
    for x in b.exidx:
        if x.kind == "inline":
            pers[f"__aeabi_unwind_cpp_pr{(x.word1 >> 24) & 0xF}"] += 1
        elif x.kind == "extab":
            w = b.u32(x.extab_addr)
            if w & 0x80000000:
                pers[f"__aeabi_unwind_cpp_pr{(w >> 24) & 0xF} (compact, in extab)"] += 1
            else:
                pa = smelf.prel31(w, x.extab_addr)
                nm = sym_at_exact(b, pa) or [hx(pa)]
                pers[f"generic personality {nm[0]}"] += 1
    exs = b.sec_by_name.get(".ARM.exidx")
    fnset = {s.value for s in b.func_symbols()}
    ex_starts = [x.fn_addr for x in b.exidx]
    out["exidx"] = {
        "section_addr": hx(exs["addr"]) if exs else None,
        "entries": len(b.exidx),
        "by_kind": dict(sorted(kinds.items())),
        "personality": dict(sorted(pers.items())),
        "sorted_ascending": ex_starts == sorted(ex_starts),
        "first_fn": hx(ex_starts[0]) if ex_starts else None, "last_fn": hx(ex_starts[-1]) if ex_starts else None,
        "entries_at_func_symbol": sum(1 for a in ex_starts if a in fnset),
        "entries_not_at_func_symbol": [hx(a) for a in ex_starts if a not in fnset],
        "extab_size": b.sec_by_name[".ARM.extab"]["size"] if ".ARM.extab" in b.sec_by_name else 0,
    }
    # .comment
    cs = b.sec_by_name.get(".comment")
    if cs:
        raw = b.data[cs["offset"]:cs["offset"] + cs["size"]]
        strs = [x.decode("latin-1") for x in raw.split(b"\x00") if x]
        out["comment"] = {"strings_total": len(strs), "unique": dict(sorted(Counter(strs).items()))}
    # mapping symbols
    ms = Counter((k, sec) for (_a, k, sec) in b.mapping_symbols)
    reg_bytes = Counter()
    for r in b.regions:
        reg_bytes[(r.section, r.kind)] += r.end - r.start
    func_odd = [s for s in b.func_symbols() if s.value & 1]
    out["mapping_symbols"] = {
        "counts": [{"kind": "$" + k, "section": sec, "count": c} for (k, sec), c in sorted(ms.items())],
        "conflicts_same_address": b.mapping_conflicts,
        "regions": len(b.regions),
        "bytes_by_section_and_kind": [{"section": s, "kind": k, "bytes": n} for (s, k), n in sorted(reg_bytes.items())],
        "func_symbols_with_odd_value(thumb)": len(func_odd),
    }
    # instruction set evidence
    insns, invalid = armdis.disassemble(b)
    census = armdis.isa_census(b, insns, invalid)
    blx_t = census["blx_immediate_sites"]
    census["interpretation"] = {
        "thumb_mapping_symbols": sum(c for (k, _s), c in ms.items() if k == "t"),
        "odd_function_symbols": len(func_odd),
        "blx_immediate_count": len(blx_t),
        "pure_arm_mode": (not func_odd and not any(k == "t" for (k, _s) in ms) and not blx_t),
        "note": ("No $t symbols, no odd FUNC values and no BLX-immediate (ARM->Thumb) calls: "
                 "the library is ARM-state only as far as static evidence goes. Indirect BX/BLX targets are "
                 "not statically provable but every resolved function pointer is even."),
    }
    out["instruction_set_evidence"] = census
    return out


def main():
    args = sys.argv[1:]
    if args == ["--all"]:
        outdir = smelf.REPO / "analysis/native"
        for n in ("v7a", "v5"):
            p = outdir / f"elf_audit.{n}.json"
            p.write_text(json.dumps(audit(n), indent=1, sort_keys=False) + "\n")
            print(f"wrote {p}")
        return
    if len(args) != 1:
        raise SystemExit(__doc__)
    json.dump(audit(args[0]), sys.stdout, indent=1)
    sys.stdout.write("\n")


if __name__ == "__main__":
    main()
