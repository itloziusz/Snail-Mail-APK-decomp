#!/usr/bin/env python3
"""Function inventory for the Snail Mail native libraries.

Usage:
    tools/decompilation/function_index.py v7a|v5|--all

Writes (per binary, deterministic, sorted by address):
    analysis/native/FUNCTION_INDEX.<bin>.jsonl   one record per unique function
                                                 start address (+ PLT stubs)
    analysis/native/function_summary.<bin>.json  denominators, coverage, gaps
    analysis/native/xrefs.<bin>.json             per-function resolved data refs,
                                                 indirect call/jump sites
    analysis/native/function_summary.json        (with --all) both summaries

Ghidra status is merged from analysis/native/ghidra_export_summary.<bin>.json
when present (status DECOMPILED = Ghidra produced C without error; says
nothing about correctness).

Classification rules (documented in docs/NATIVE_ANALYSIS.md):
  jni     : name starts with "Java_" (JNI-exported native method)
  runtime : libgcc / ARM EABI helper (RUNTIME_RE below); must lie in the
            contiguous libgcc tail of .text (checked, exceptions reported)
  plt     : PLT stub decoded from .plt (not a symbol; one per JUMP_SLOT)
  game    : every other defined FUNC symbol (application C/C++ code)
  component (heuristic, name-based, secondary): see COMPONENT_RULES
"""
from __future__ import annotations

import bisect
import json
import re
import sys
from collections import Counter, defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import armdis  # noqa: E402
import smelf  # noqa: E402
from smelf import hx  # noqa: E402

OUT = smelf.REPO / "analysis/native"

RUNTIME_RE = re.compile(
    r"^(__aeabi_\w+|__(u?div|u?mod)(si|di)3|__div0|__gnu_\w+|_Unwind_\w+|___Unwind_\w+|"
    r"__(add|sub|mul|div|neg)(sf|df)3|__(neg)(sf|df)2|__(cmp|eq|ne|lt|le|gt|ge|unord)(sf|df)2|"
    r"__fix(uns)?(sf|df)(si|di)|__float(un)?(si|di)(sf|df)|__extendsfdf2|__truncdfsf2|"
    r"__(ashl|ashr|lshr|mul|cmp|ucmp)di[23]|__negdi2|__clz\w*|__ffs\w*|__popcount\w*|"
    r"__restore_core_regs|restore_core_regs|restore_non_core_regs|selfrel_offset31|search_EIT_table|"
    r"get_eit_entry|unwind_phase2|unwind_phase2_forced|next_unwind_byte|unwind_UCB_from_context|"
    r"__cxa_\w+|__gxx_personality\w*|__on_dlclose|__atexit_handler_wrapper)$")

# (regex on raw symbol name, component) - first match wins. Heuristic labels only.
COMPONENT_RULES = [
    (r"^Java_", "jni_export"),
    (r"^_ZN7_JNIEnv", "jni_header_inline"),
    (r"^(_Z\d+)?JAVA", "java_bridge"),
    (r"^(_Z\d+)?JNI", "java_bridge"),
    (r"^_GLOBAL__I_", "static_init"),
    (r"OpenFeint|^(_Z\d+)?OFO|OFO", "openfeint"),
    (r"^(_Z\d+)?RShell", "rshell_platform"),
    (r"^(_Z\d+)?Pfm", "pfm_platform"),
    (r"^_ZN\d+cRResourceManager|^_ZN\d+cRHash", "resource_archive"),
    (r"^(_Z\d+)?(importGL|appInit|appRender|appDeinit|AppInit|_getTime)|^_ZL8_getTime", "app_glue"),
    (r"^(_Z\d+)?(GL|InitGL|gl)", "gl_wrapper"),
    (r"^_Z\d+OSD|^(_Z\d+)?OSD", "osd_text"),
    (r"^_ZN\d+cR", "engine_class_cR"),
    (r"^_ZN\d+c[A-Z]", "class_c"),
    (r"^_ZN\d+t[A-Z]", "type_t"),
    (r"^_Z\d+ObjectProc", "object_proc"),
]
COMPONENT_RULES = [(re.compile(r), c) for r, c in COMPONENT_RULES]


def component_of(name):
    for rx, c in COMPONENT_RULES:
        if rx.search(name):
            return c
    return "other"


def pick_primary(syms):
    """Deterministic primary-name choice among same-address aliases."""
    bind_rank = {"STB_GLOBAL": 0, "STB_WEAK": 1, "STB_LOCAL": 2}

    def key(s):
        lead = len(s.name) - len(s.name.lstrip("_"))
        return (0 if s.size else 1, bind_rank.get(s.bind, 3), lead, s.name)
    return sorted(syms, key=key)[0]


class SymIndex:
    """Address -> containing symbol (OBJECT/FUNC with size; exact match for size 0)."""

    def __init__(self, b):
        ent = []
        for s in b.symtab:
            if not s.name or not b.defined(s) or s.type not in ("STT_OBJECT", "STT_FUNC", "STT_NOTYPE"):
                continue
            if s.name.startswith("$"):
                continue
            ent.append((s.value, s.value + max(s.size, 1), s.name, s.type, s.size))
        ent.sort(key=lambda e: (e[0], -e[1], e[2]))
        self.ent = ent
        self.starts = [e[0] for e in ent]
        self.b = b

    def lookup(self, addr):
        i = bisect.bisect_right(self.starts, addr) - 1
        best = None
        # scan back a little for enclosing sized symbols
        j = i
        while j >= 0 and j > i - 64:
            s, e, n, t, sz = self.ent[j]
            if s <= addr < e and t != "STT_NOTYPE":
                if best is None or (s > best[0]):
                    best = (s, n, t, sz)
            j -= 1
        if best is None and i >= 0 and self.ent[i][0] == addr:
            s, e, n, t, sz = self.ent[i]
            best = (s, n, t, sz)
        return best

    def describe(self, addr):
        if not isinstance(addr, int):
            return None
        hit = self.lookup(addr)
        sec = self.b.section_of(addr)
        d = {"addr": hx(addr), "section": sec}
        if hit:
            s, n, t, sz = hit
            d["symbol"] = n if addr == s else f"{n}+0x{addr - s:x}"
            d["symbol_type"] = t
        if sec in (".rodata",) and "symbol" not in d:
            st = self.b.cstring(addr, 512)
            if st is not None and len(st) >= 1 and all(32 <= ord(c) < 127 or c in "\n\t\r" for c in st):
                d["string"] = st[:160]
        return d


def load_ghidra_ok(binname):
    p = OUT / f"ghidra_export_summary.{binname}.json"
    if not p.exists():
        return None, None
    s = json.loads(p.read_text())
    return set(int(a, 16) for a in s.get("decompiled_ok", [])), s


def build(binname):
    b = smelf.Binary(binname)
    insns, invalid = armdis.disassemble(b)
    invalid_addrs = sorted(x["addr"] for x in invalid)
    resolver = armdis.Resolver(b, insns)
    symidx = SymIndex(b)
    exported = b.exported_names()
    ghidra_ok, ghidra_sum = load_ghidra_ok(binname)

    raw_funcs = b.func_symbols()
    by_addr = defaultdict(list)
    for s in raw_funcs:
        by_addr[s.value].append(s)
    starts = sorted(by_addr)
    start_set = set(starts)
    text_lo, text_hi = b.text_range()
    demangled = smelf.demangle_many(sorted({s.name for s in raw_funcs}))

    # runtime tail: first address whose symbol matches RUNTIME_RE and after which all do
    runtime_flags = {a: bool(RUNTIME_RE.match(pick_primary(by_addr[a]).name)) for a in starts}
    tail_start = None
    for a in reversed(starts):
        if runtime_flags[a]:
            tail_start = a
        else:
            break

    records = []
    fn_ranges = []
    for idx, a in enumerate(starts):
        syms = by_addr[a]
        prim = pick_primary(syms)
        size = max(s.size for s in syms)
        questions = []
        size_source = "symbol"
        if size == 0:
            nxt = starts[idx + 1] if idx + 1 < len(starts) else text_hi
            reg = b.region_at(a)
            size = nxt - a
            size_source = "next_symbol"
            questions.append(f"all symbols at this address have st_size 0; size taken as distance to next FUNC symbol (0x{nxt:x})")
        end = a + size
        fn_ranges.append((a, end))
        # overlaps
        inner = [s2 for s2 in starts[idx + 1:idx + 50] if s2 < end]
        if inner and size_source == "symbol":
            questions.append("function range contains other FUNC symbol starts: " + ", ".join(hx(x) for x in inner))
        # exidx agreement
        ex = b.exidx_by_fn.get(a)
        j = bisect.bisect_right(b.exidx_starts, a)
        next_ex = b.exidx_starts[j] if j < len(b.exidx_starts) else None
        if size_source != "symbol":
            bconf = "heuristic"
        elif ex is not None and (next_ex is None or next_ex >= end):
            bconf = "symbol+exidx"
        else:
            bconf = "symbol"
            if ex is not None and next_ex is not None and next_ex < end:
                questions.append(f"EXIDX entry at 0x{next_ex:x} starts inside the symbol range")
        # mode
        reg = b.region_at(a)
        mode = "thumb" if (a & 1 or (reg and reg.kind == "thumb")) else ("arm" if reg and reg.kind == "arm" else (reg.kind if reg else "unmapped"))
        # coverage check for DISASSEMBLED
        cover_ok = size > 0
        k = a
        while cover_ok and k < end:
            r = b.region_at(k)
            if r is None or r.kind == "unmapped":
                cover_ok = False
                break
            k = r.end
        bad = [x for x in invalid_addrs if a <= x < end]
        status = ["DISCOVERED"]
        if cover_ok and not bad:
            status.append("DISASSEMBLED")
        if ghidra_ok is not None and a in ghidra_ok:
            status.append("DECOMPILED")
        # category
        name = prim.name
        if name.startswith("Java_"):
            cat = "jni"
        elif runtime_flags[a] or (tail_start is not None and a >= tail_start):
            cat = "runtime"
        else:
            cat = "game"
        fa = resolver.analyze(a, end, start_set)
        rec = {
            "binary_sha256": b.sha256, "binary": binname, "addr": hx(a),
            "file_offset": hx(b.vaddr_to_offset(a)), "size": size, "size_source": size_source,
            "mode": mode, "name": name, "demangled": demangled.get(name, name), "alias": None,
            "same_address_symbols": sorted(s.name for s in syms if s.name != name),
            "binding": prim.bind, "exported": any(s.name in exported for s in syms),
            "category": cat, "component_hint": component_of(name) if cat == "game" else cat,
            "boundary_confidence": bconf,
            "exidx": ({"entry": hx(ex.entry_addr), "kind": ex.kind} if ex else None),
            "status": status, "evidence": [],
            "insn_count": fa.insn_count,
            "_fa": fa,
            "questions": questions,
        }
        records.append(rec)

    # PLT stubs as separate records
    plt_records = []
    for p in b.plt:
        plt_records.append({
            "binary_sha256": b.sha256, "binary": binname, "addr": hx(p.addr),
            "file_offset": hx(b.vaddr_to_offset(p.addr)), "size": p.size, "size_source": "plt_decode",
            "mode": "arm", "name": f"{p.import_name}@plt" if p.import_name else f"plt_0x{p.addr:x}",
            "demangled": f"{smelf.demangle_many([p.import_name])[p.import_name]}@plt" if p.import_name else None,
            "alias": None, "same_address_symbols": [], "binding": None, "exported": False,
            "category": "plt", "component_hint": "plt", "boundary_confidence": "plt-decode",
            "exidx": None, "status": ["DISCOVERED", "DISASSEMBLED"], "evidence": [],
            "insn_count": 3, "got_slot": hx(p.got_slot), "import": p.import_name,
            "questions": [],
        })
    plt_by_addr = {p.addr: p for p in b.plt}

    # callers/callees
    callers = defaultdict(set)
    addr_taken_by = defaultdict(set)
    xrefs = []
    mid_targets_total = []
    rec_by_addr = {int(r["addr"], 16): r for r in records}
    for r in records:
        a = int(r["addr"], 16)
        fa = r.pop("_fa")
        callees, tails, imports, mid = set(), set(), set(), []
        for site, t in fa.calls + fa.tailcalls:
            if t in plt_by_addr:
                imports.add(plt_by_addr[t].import_name or hx(t))
                callers[t].add(a)
            elif t in start_set:
                (tails if (site, t) in fa.tailcalls else callees).add(t)
                callers[t].add(a)
            else:
                mid.append({"site": hx(site), "target": hx(t), "container": symidx.describe(t).get("symbol")})
        for site, t in fa.external_jumps:
            mid.append({"site": hx(site), "target": hx(t), "container": symidx.describe(t).get("symbol"), "kind": "branch"})
        mid_targets_total.extend(mid)
        # data refs
        refs_out = []
        reads, writes, addrs = set(), set(), set()
        for ref in fa.refs:
            d = symidx.describe(ref["addr"]) if isinstance(ref["addr"], int) else None
            if d is None:
                continue
            label = d.get("symbol") or (("str:" + json.dumps(d["string"])) if "string" in d else f'{d["section"]}:{d["addr"]}')
            refs_out.append({"site": hx(ref["site"]), "kind": ref["kind"], "via": ref["via"], **d})
            if ref["via"] in ("got", "rel_ro") and ref["kind"] == "read":
                continue  # the GOT slot read itself is plumbing; keep in xrefs only
            base = label.split("+")[0]
            if ref["kind"] == "read":
                reads.add(base)
            elif ref["kind"] == "write":
                writes.add(base)
            else:
                addrs.add(base)
                if d.get("symbol_type") == "STT_FUNC" and "+" not in d.get("symbol", "+"):
                    addr_taken_by[int(d["addr"], 16)].add(a)
        ind_calls = []
        for ic in fa.indirect_calls:
            res = ic["resolved"]
            rd = None
            if res:
                rd = {}
                if "slot" in res:
                    rd["slot"] = symidx.describe(res["slot"])
                if res.get("addr") is not None:
                    rd["target"] = symidx.describe(res["addr"])
                if "import" in res:
                    rd["import"] = res["import"]
                if "base" in res:
                    rd["base"] = symidx.describe(res["base"])
            ind_calls.append({"site": hx(ic["site"]), "insn": ic["text"], "idiom": ic["idiom"], "expr": ic["expr"], "resolved": rd})
        ind_jumps = [{"site": hx(j["site"]), "insn": j["text"], "kind": j["kind"], "targets": j["targets"]} for j in fa.indirect_jumps]
        r["callees"] = sorted(hx(x) for x in callees)
        r["tailcallees"] = sorted(hx(x) for x in tails)
        r["imports_called"] = sorted(imports)
        r["indirect_call_sites"] = len(fa.indirect_calls)
        r["indirect_call_sites_resolved"] = sum(1 for x in ind_calls if x["resolved"] and ("target" in x["resolved"] or "import" in x["resolved"]))
        r["indirect_jump_sites"] = len(fa.indirect_jumps)
        r["switch_tables"] = sum(1 for j in fa.indirect_jumps if j["kind"] == "switch_table")
        r["globals_read"] = sorted(reads)
        r["globals_written"] = sorted(writes)
        r["addresses_taken"] = sorted(addrs)
        r["branch_targets_not_function_start"] = mid
        if mid:
            r["questions"].append("direct branch/call to an address that is not a FUNC symbol start")
        xrefs.append({"addr": r["addr"], "name": r["name"], "refs": refs_out,
                      "indirect_calls": ind_calls, "indirect_jumps": ind_jumps,
                      "literal_loads": len(fa.literals)})
    for r in records + plt_records:
        a = int(r["addr"], 16)
        r["callers"] = sorted(hx(x) for x in callers.get(a, ()))
        r["address_taken_by"] = sorted(hx(x) for x in addr_taken_by.get(a, ()))
    for r in plt_records:
        r["callees"], r["tailcallees"], r["imports_called"] = [], [], [r["import"]] if r["import"] else []
        r["indirect_call_sites"] = 0
        r["indirect_jump_sites"] = 1  # ldr pc, [ip, #..]! through the GOT
    all_recs = sorted(records + plt_records, key=lambda r: int(r["addr"], 16))

    # ---------------------------------------------------------------- summary
    covered = sorted(fn_ranges)
    gaps = []
    cur = text_lo
    for s, e in covered:
        if s > cur:
            gaps.append((cur, s))
        cur = max(cur, e)
    if cur < text_hi:
        gaps.append((cur, text_hi))
    gap_list = []
    for s, e in gaps:
        bts = b.read(s, e - s)
        kinds = sorted({(b.region_at(x).kind if b.region_at(x) else "unmapped") for x in range(s, e, 4)})
        first = insns.get(s)
        gap_list.append({
            "start": hx(s), "end": hx(e), "size": e - s, "region_kinds": kinds,
            "all_zero": not any(bts), "first_insn": first.text if first else None,
            "exidx_entry_at_start": s in b.exidx_by_fn,
            "interpretation": ("alignment padding (zero bytes)" if not any(bts) else
                               ("possible unnamed code" if "arm" in kinds else "data (literal/table) outside any symbol range")),
        })
    cats = Counter(r["category"] for r in records)
    status_counts = Counter(s for r in records for s in r["status"])
    bconf = Counter(r["boundary_confidence"] for r in records)
    comp = Counter(r["component_hint"] for r in records)
    alias_groups = [{"addr": hx(a), "symbols": sorted(s.name for s in by_addr[a])} for a in starts if len(by_addr[a]) > 1]
    runtime_outside_tail = [r["addr"] for r in records if r["category"] == "runtime" and tail_start is not None and int(r["addr"], 16) < tail_start]
    runtime_by_position_only = [r["name"] for r in records if r["category"] == "runtime" and not RUNTIME_RE.match(r["name"])]
    ind_total = sum(r["indirect_call_sites"] for r in records)
    ind_res = sum(r["indirect_call_sites_resolved"] for r in records)
    exidx_fn = sum(1 for r in records if r["exidx"])
    summary = {
        "binary": binname, "binary_sha256": b.sha256,
        "denominators": {
            "raw_defined_FUNC_symbols_symtab": len(raw_funcs),
            "raw_defined_FUNC_symbols_dynsym": sum(1 for s in b.dynsym if s.type == "STT_FUNC" and b.defined(s)),
            "unique_function_start_addresses": len(starts),
            "same_address_alias_groups": len(alias_groups),
            "zero_size_symbol_addresses": sum(1 for r in records if r["size_source"] != "symbol"),
            "plt_stubs": len(plt_records),
            "note": "raw FUNC symbol counts include aliases and libgcc/EABI helpers; they are NOT a count of recovered game functions. Use by_category.game.",
        },
        "by_category": dict(sorted(cats.items())),
        "by_component_hint": dict(sorted(comp.items())),
        "runtime_rule": {"libgcc_tail_start": hx(tail_start), "runtime_outside_tail": runtime_outside_tail,
                         "runtime_by_position_only": runtime_by_position_only},
        "boundary_confidence": dict(sorted(bconf.items())),
        "functions_with_exidx_entry": exidx_fn,
        "exidx_entries_total": len(b.exidx),
        "status_counts": dict(sorted(status_counts.items())),
        "ghidra_merge": ({"summary_file": f"analysis/native/ghidra_export_summary.{binname}.json",
                          "ghidra_functions_processed": ghidra_sum.get("functions_processed"),
                          "ghidra_functions_without_elf_symbol": ghidra_sum.get("functions_without_elf_symbol_count"),
                          "ghidra_functions_without_elf_symbol_outside_plt":
                              [x for x in ghidra_sum.get("functions_without_elf_symbol", []) if x.get("block") != ".plt"]}
                         if ghidra_sum else None),
        "text": {"start": hx(text_lo), "end": hx(text_hi), "size": text_hi - text_lo,
                 "bytes_covered_by_function_symbols": sum(min(e, text_hi) - s for s, e in _merge(covered)),
                 "gap_count": len(gap_list), "gap_bytes": sum(g["size"] for g in gap_list)},
        "gaps": gap_list,
        "instructions_decoded": len(insns), "invalid_decodes": len(invalid),
        "calls": {
            "direct_call_edges_unique": sum(len(r["callees"]) for r in records),
            "tailcall_edges_unique": sum(len(r["tailcallees"]) for r in records),
            "indirect_call_sites": ind_total,
            "indirect_call_sites_statically_resolved": ind_res,
            "indirect_jump_sites": sum(r["indirect_jump_sites"] for r in records),
            "switch_tables": sum(r["switch_tables"] for r in records),
            "branch_targets_not_function_start": mid_targets_total,
        },
        "alias_groups": alias_groups,
    }
    return b, all_recs, summary, xrefs


def _merge(ranges):
    out = []
    for s, e in sorted(ranges):
        if out and s <= out[-1][1]:
            out[-1] = (out[-1][0], max(out[-1][1], e))
        else:
            out.append((s, e))
    return out


def write(binname):
    b, recs, summary, xrefs = build(binname)
    p = OUT / f"FUNCTION_INDEX.{binname}.jsonl"
    with open(p, "w") as f:
        for r in recs:
            f.write(json.dumps(r, sort_keys=False) + "\n")
    (OUT / f"function_summary.{binname}.json").write_text(json.dumps(summary, indent=1) + "\n")
    (OUT / f"xrefs.{binname}.json").write_text(json.dumps(
        {"binary": binname, "binary_sha256": b.sha256,
         "method": "tools/decompilation/armdis.py Resolver: static constant propagation (literal pools, PC/GOT-relative); static inference, re-check by hand",
         "functions": xrefs}, indent=0) + "\n")
    print(f"wrote {p} ({len(recs)} records)")
    return summary


def main():
    args = sys.argv[1:]
    if args == ["--all"]:
        sums = {n: write(n) for n in ("v7a", "v5")}
        (OUT / "function_summary.json").write_text(json.dumps(
            {"note": "per-binary summaries; see function_summary.<bin>.json for full gap lists",
             "binaries": {n: {k: v for k, v in s.items() if k not in ("gaps", "alias_groups", "calls")} |
                          {"calls": {k: v for k, v in s["calls"].items() if k != "branch_targets_not_function_start"}}
                          for n, s in sums.items()}}, indent=1) + "\n")
        return
    if len(args) != 1:
        raise SystemExit(__doc__)
    write(args[0])


if __name__ == "__main__":
    main()
