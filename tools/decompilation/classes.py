#!/usr/bin/env python3
"""C++ class / vtable / typeinfo / global-object inventory from ELF symbols.

Usage:
    tools/decompilation/classes.py v7a|v5 [OUT.json]
    default OUT: analysis/native/classes.<bin>.json

Sources (all static, symbol-driven; no decompiler output is used):
  * .symtab FUNC symbols with Itanium-mangled nested names (_ZN..., _ZNK...)
    -> class/namespace -> methods; ctor/dtor variants from C1/C2/C3/D0/D1/D2
  * _ZTV* objects: every slot word read from the file and resolved through
    dynamic relocations (R_ARM_RELATIVE / ABS32) -> function symbol
  * _ZTI* / _ZTS* objects (typeinfo); absence is reported (=> -fno-rtti)
  * STT_OBJECT symbols with sizes, largest first, and a .bss coverage map
  * functions that materialise a vtable address (constructors, per
    analysis/native/xrefs.<bin>.json from function_index.py) when available
Note: a mangled nested name does not distinguish a class from a namespace;
"kind" is "class" only when a ctor/dtor/vtable/const-method proves it.
"""
from __future__ import annotations

import json
import re
import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import smelf  # noqa: E402
from smelf import hx  # noqa: E402

OUT = smelf.REPO / "analysis/native"
CDTOR_RE = re.compile(r"(C[1-3]|D[0-2])E")


def split_qualified(dem: str):
    """'A::B::m(int) const' -> (['A','B','m'], '(int) const'). Template-aware."""
    depth = 0
    cut = len(dem)
    for i, ch in enumerate(dem):
        if ch == "<":
            depth += 1
        elif ch == ">":
            depth -= 1
        elif ch == "(" and depth == 0:
            cut = i
            break
    q, params = dem[:cut], dem[cut:]
    parts, depth, cur = [], 0, ""
    i = 0
    while i < len(q):
        ch = q[i]
        if ch == "<":
            depth += 1
        elif ch == ">":
            depth -= 1
        if depth == 0 and q.startswith("::", i):
            parts.append(cur)
            cur = ""
            i += 2
            continue
        cur += ch
        i += 1
    parts.append(cur)
    return parts, params


def build(binname):
    b = smelf.Binary(binname)
    syms = [s for s in b.symtab if s.name and b.defined(s) and not s.name.startswith("$")]
    dem = smelf.demangle_many(sorted({s.name for s in syms}))
    func_at = defaultdict(list)
    for s in syms:
        if s.type == "STT_FUNC":
            func_at[s.value].append(s.name)
    for k in func_at:
        func_at[k].sort()

    # xrefs (optional) for vtable materialisation sites
    xr_path = OUT / f"xrefs.{binname}.json"
    vt_refs = defaultdict(set)
    if xr_path.exists():
        xr = json.loads(xr_path.read_text())
        for f in xr["functions"]:
            for r in f["refs"]:
                if r.get("symbol", "").startswith("_ZTV") and r["kind"] == "addr":
                    vt_refs[r["symbol"].split("+")[0]].add((f["addr"], f["name"]))

    # ---- classes from nested names
    classes = defaultdict(lambda: {"methods": [], "ctors": [], "dtors": [], "evidence_kind": set()})
    free_funcs = 0
    for s in sorted((s for s in syms if s.type == "STT_FUNC"), key=lambda s: (s.value, s.name)):
        if not (s.name.startswith("_ZN") or s.name.startswith("_ZNK")):
            if s.name.startswith("_Z"):
                free_funcs += 1
            continue
        d = dem[s.name]
        parts, params = split_qualified(d)
        if len(parts) < 2:
            continue
        cls = "::".join(parts[:-1])
        meth = parts[-1]
        rec = {"addr": hx(s.value), "size": s.size, "name": s.name, "demangled": d, "method": meth,
               "const": s.name.startswith("_ZNK") or params.rstrip().endswith("const")}
        m = CDTOR_RE.search(s.name[3:])
        last = parts[-2].split("<")[0]
        if meth == last:
            rec["variant"] = m.group(1) if m else None
            rec["variant_meaning"] = {"C1": "complete object ctor", "C2": "base object ctor",
                                      "C3": "allocating ctor"}.get(rec["variant"])
            classes[cls]["ctors"].append(rec)
            classes[cls]["evidence_kind"].add("ctor")
        elif meth.startswith("~"):
            rec["variant"] = m.group(1) if m else None
            rec["variant_meaning"] = {"D0": "deleting dtor", "D1": "complete object dtor",
                                      "D2": "base object dtor"}.get(rec["variant"])
            classes[cls]["dtors"].append(rec)
            classes[cls]["evidence_kind"].add("dtor")
        else:
            classes[cls]["methods"].append(rec)
            if rec["const"]:
                classes[cls]["evidence_kind"].add("const_method")

    # ---- vtables
    vtables = []
    for s in sorted((s for s in syms if s.name.startswith("_ZTV")), key=lambda s: s.value):
        cls = dem[s.name].replace("vtable for ", "")
        slots = []
        for k in range(s.size // 4):
            va = s.value + 4 * k
            rw = b.resolve_word(va)
            val = rw["value"]
            ent = {"index": k, "slot_addr": hx(va), "raw": hx(rw["raw"]), "reloc": rw["kind"],
                   "value": hx(val) if val is not None else None}
            if k == 0:
                ent["role"] = "offset_to_top"
            elif k == 1:
                ent["role"] = "typeinfo_ptr"
            else:
                ent["role"] = f"virtual_slot_{k - 2}"
                ent["vptr_offset"] = 4 * (k - 2)
            if rw["kind"] == "import":
                ent["import"] = rw["symbol"]
            if val and val in func_at:
                ent["function"] = func_at[val][0]
                ent["function_demangled"] = dem[func_at[val][0]]
                ent["aliases"] = func_at[val][1:]
            slots.append(ent)
        vt = {"symbol": s.name, "class": cls, "addr": hx(s.value), "size": s.size, "section": s.section,
              "binding": s.bind, "slots": slots,
              "typeinfo_present": any(e["role"] == "typeinfo_ptr" and e["value"] not in (None, "0x0") for e in slots),
              "materialised_by": [{"addr": a, "name": n, "demangled": dem.get(n, n)} for a, n in sorted(vt_refs.get(s.name, ()))]}
        vtables.append(vt)
        classes[cls]["evidence_kind"].add("vtable")
        classes[cls]["vtable"] = s.name
    typeinfo = [{"symbol": s.name, "demangled": dem[s.name], "addr": hx(s.value), "size": s.size, "section": s.section}
                for s in sorted(syms, key=lambda s: s.value) if s.name.startswith(("_ZTI", "_ZTS"))]

    class_list = []
    for cls in sorted(classes):
        c = classes[cls]
        kind = "class" if c["evidence_kind"] else "class_or_namespace"
        class_list.append({
            "name": cls, "kind": kind, "evidence": sorted(c["evidence_kind"]),
            "vtable": c.get("vtable"),
            "ctors": sorted(c["ctors"], key=lambda r: int(r["addr"], 16)),
            "dtors": sorted(c["dtors"], key=lambda r: int(r["addr"], 16)),
            "methods": sorted(c["methods"], key=lambda r: int(r["addr"], 16)),
            "method_count": len(c["methods"]) + len(c["ctors"]) + len(c["dtors"]),
        })

    # ---- global objects
    objs = []
    for s in syms:
        if s.type != "STT_OBJECT":
            continue
        objs.append({"name": s.name, "demangled": dem[s.name], "addr": hx(s.value), "size": s.size,
                     "section": s.section, "binding": s.bind})
    objs.sort(key=lambda o: (-o["size"], int(o["addr"], 16), o["name"]))

    # ---- .bss coverage
    bss = b.sec_by_name[".bss"]
    lo, hi = bss["addr"], bss["addr"] + bss["size"]
    in_bss = sorted(((int(o["addr"], 16), o["size"], o["name"]) for o in objs if o["section"] == ".bss"))
    covered, cur, gaps = 0, lo, []
    for a, sz, n in in_bss:
        if a > cur:
            gaps.append((cur, a))
        covered += max(0, min(a + sz, hi) - max(a, cur))
        cur = max(cur, a + sz)
    if cur < hi:
        gaps.append((cur, hi))
    # label gaps with preceding NOTYPE/size-0 symbols (e.g. local statics)
    notype = sorted(((s.value, s.name, s.type) for s in syms if s.section == ".bss" and s.type != "STT_OBJECT"))
    gap_list = []
    for gs, ge in sorted(gaps, key=lambda g: -(g[1] - g[0])):
        prev = [o for o in in_bss if o[0] + o[1] <= gs]
        labels = [n for (v, n, t) in notype if gs <= v < ge]
        gap_list.append({"start": hx(gs), "end": hx(ge), "size": ge - gs,
                         "preceding_object": prev[-1][2] if prev else None,
                         "symbols_inside": labels[:10]})
    bss_summary = {
        "section_addr": hx(lo), "section_size": bss["size"], "section_size_hex": hx(bss["size"]),
        "sized_object_symbols": len(in_bss), "bytes_covered_by_sized_objects": covered,
        "bytes_not_covered": bss["size"] - covered,
        "largest_uncovered_ranges": gap_list[:25],
        "top_objects": [o for o in objs if o["section"] == ".bss"][:40],
        "note": ("Uncovered .bss ranges have no sized OBJECT symbol (e.g. file-local statics whose symbols "
                 "were not emitted, or padding). Attribute them via the functions that address them "
                 "(analysis/native/xrefs.<bin>.json) before trusting any layout."),
    }
    return {
        "binary": binname, "binary_sha256": b.sha256,
        "method": "symbol-driven (.symtab + dynamic relocations); tools/decompilation/classes.py",
        "counts": {
            "nested_name_scopes": len(class_list),
            "proven_classes": sum(1 for c in class_list if c["kind"] == "class"),
            "vtables": len(vtables), "typeinfo_symbols": len(typeinfo),
            "free_mangled_functions": free_funcs,
            "object_symbols": len(objs),
            "object_bytes_by_section": {sec: sum(o["size"] for o in objs if o["section"] == sec)
                                        for sec in sorted({o["section"] for o in objs if o["section"]})},
        },
        "rtti": ("absent: no _ZTI/_ZTS symbols and vtable typeinfo slots are 0 (consistent with -fno-rtti)"
                 if not typeinfo and not any(v["typeinfo_present"] for v in vtables) else "present"),
        "classes": class_list,
        "vtables": vtables,
        "typeinfo": typeinfo,
        "bss": bss_summary,
        "objects": objs,
    }


def main():
    if len(sys.argv) < 2:
        raise SystemExit(__doc__)
    binname = sys.argv[1]
    out = Path(sys.argv[2]) if len(sys.argv) > 2 else OUT / f"classes.{binname}.json"
    data = build(binname)
    out.write_text(json.dumps(data, indent=1) + "\n")
    c = data["counts"]
    print(f"wrote {out}: {c['nested_name_scopes']} scopes ({c['proven_classes']} proven classes), "
          f"{c['vtables']} vtables, {c['typeinfo_symbols']} typeinfo, {c['object_symbols']} objects")


if __name__ == "__main__":
    main()
