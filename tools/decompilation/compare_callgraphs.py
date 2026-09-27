#!/usr/bin/env python3
"""Cross-check the capstone call graph (FUNCTION_INDEX.<bin>.jsonl) against
Ghidra's (analysis/native/generated/<bin>_callgraph.json).

Usage: tools/decompilation/compare_callgraphs.py v7a|v5
Writes analysis/native/callgraph_crosscheck.<bin>.json

Edges compared: (caller entry, callee entry) for direct calls and tail calls
between defined functions and to PLT stubs. Ghidra edges to its EXTERNAL
placeholders are mapped back to the PLT stub through the import name.
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import smelf  # noqa: E402

OUT = smelf.REPO / "analysis/native"


def main():
    if len(sys.argv) != 2:
        raise SystemExit(__doc__)
    binname = sys.argv[1]
    recs = [json.loads(line) for line in open(OUT / f"FUNCTION_INDEX.{binname}.jsonl")]
    plt_by_import = {r["import"]: int(r["addr"], 16) for r in recs if r["category"] == "plt" and r.get("import")}
    ours = set()
    for r in recs:
        if r["category"] == "plt":
            continue
        a = int(r["addr"], 16)
        for c in r["callees"] + r["tailcallees"]:
            ours.add((a, int(c, 16)))
        for imp in r["imports_called"]:
            if imp in plt_by_import:
                ours.add((a, plt_by_import[imp]))
    cg_path = OUT / "generated" / f"{binname}_callgraph.json"
    if not cg_path.exists():
        raise SystemExit(f"{cg_path} missing: run tools/decompilation/run_ghidra.sh {binname} export")
    cg = json.loads(cg_path.read_text())
    starts = {int(r["addr"], 16) for r in recs}
    plt_starts = {int(r["addr"], 16) for r in recs if r["category"] == "plt"}
    theirs = set()
    unresolved = 0
    for f in cg["functions"]:
        a = int(f["addr"], 16)
        if a not in starts or a in plt_starts:  # PLT thunk -> EXTERNAL edges are Ghidra plumbing
            continue
        for e in f["calls"]:
            if e["to"] is None:
                unresolved += 1
                continue
            t = int(e["to"], 16)
            if t not in starts:
                imp = (e.get("thunk_target") or e.get("to_name") or "").replace("<EXTERNAL>::", "")
                t = plt_by_import.get(imp, t)
            theirs.add((a, t))
    only_ours = sorted(ours - theirs)
    only_theirs = sorted(theirs - ours)
    name = {int(r["addr"], 16): r["name"] for r in recs}

    def fmt(edges):
        return [{"caller": f"0x{a:x}", "caller_name": name.get(a), "callee": f"0x{b:x}", "callee_name": name.get(b)}
                for a, b in edges]
    res = {
        "binary": binname, "binary_sha256": recs[0]["binary_sha256"],
        "capstone_edges": len(ours), "ghidra_edges": len(theirs), "common": len(ours & theirs),
        "only_capstone": fmt(only_ours), "only_ghidra": fmt(only_theirs),
        "ghidra_unresolved_computed_calls": unresolved,
    }
    p = OUT / f"callgraph_crosscheck.{binname}.json"
    p.write_text(json.dumps(res, indent=1) + "\n")
    print(f"wrote {p}: capstone={len(ours)} ghidra={len(theirs)} common={len(ours & theirs)} "
          f"only_capstone={len(only_ours)} only_ghidra={len(only_theirs)} unresolved={unresolved}")


if __name__ == "__main__":
    main()
