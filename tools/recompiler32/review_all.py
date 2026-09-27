#!/usr/bin/env python3
"""Cross-check every indexed ARM32 function against ELF, unwind and AOT evidence.

Produces a per-function review ledger. It does not infer behavior from a symbol
name, equate successful C generation with correct gameplay, or promote an
unmatched code address to a function without independent boundary evidence.
"""

import argparse
from collections import Counter, defaultdict
import hashlib
import json
from pathlib import Path
import re
import struct

from elftools.elf.elffile import ELFFile


ROOT = Path(__file__).resolve().parents[2]


def read_json(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def exidx_starts(elf: ELFFile) -> set[int]:
    section = elf.get_section_by_name(".ARM.exidx")
    if section is None or section["sh_size"] % 8:
        raise ValueError("missing or malformed .ARM.exidx")
    starts = set()
    for i, (prel, _) in enumerate(struct.iter_unpack("<II", section.data())):
        displacement = prel & 0x7FFFFFFF
        if displacement & 0x40000000:
            displacement -= 0x80000000
        starts.add(section["sh_addr"] + i * 8 + displacement)
    return starts


def reference_suites(path: Path, binary_sha256: str) -> dict[int, list[str]]:
    result = read_json(path)
    if result["reference"]["sha256"] != binary_sha256:
        raise ValueError("reference suite hash does not match binary")
    suites = defaultdict(list)
    for suite in result["suites"]:
        if suite.get("mismatches") != 0:
            continue
        for function in suite["original_functions"]:
            match = re.search(r"v7a:(0x[0-9a-f]+(?:-0x[0-9a-f]+)?)", function)
            if match and "-" not in match.group(1):
                suites[int(match.group(1), 16)].append(suite["name"])
    return {addr: sorted(set(names)) for addr, names in suites.items()}


def matching_names(index_row: dict, lowered_row: dict) -> bool:
    """Accept an AOT-selected ELF alias only when the index records it."""
    names = {index_row["name"], *index_row.get("same_address_symbols", [])}
    return lowered_row["name"] in names


def review(elf_path: Path, index_path: Path, manifest_path: Path,
           summary_path: Path, evidence_path: Path, differential_path: Path) -> tuple[dict, list[dict], list[dict]]:
    binary_sha256 = hashlib.sha256(elf_path.read_bytes()).hexdigest()
    index = [json.loads(line) for line in index_path.read_text(encoding="utf-8").splitlines() if line]
    manifest = read_json(manifest_path)
    source_summary = read_json(summary_path)
    index_hashes = {row["binary_sha256"] for row in index}
    if len(index_hashes) != 1:
        raise ValueError("function index must have exactly one binary hash")
    for label, actual in (("AOT manifest", manifest["binary_sha256"]),
                          ("function index", next(iter(index_hashes))),
                          ("function summary", source_summary["binary_sha256"])):
        if actual != binary_sha256:
            raise ValueError(f"{label} hash does not match ELF")

    indexed = [row for row in index if row["category"] != "plt"]
    by_address = {int(row["addr"], 16): row for row in indexed}
    lowered = {int(row["addr"], 16): (i, row)
               for i, row in enumerate(manifest["functions"])}
    if len(by_address) != len(indexed) or set(by_address) != set(lowered):
        raise ValueError("unique indexed starts and translated function starts differ")
    if sum(len(row["unsupported"]) for row in manifest["functions"]) != manifest["unsupported_sites"]:
        raise ValueError("AOT unsupported-site count differs from manifest")
    chunks = {}
    port_sources = "\n".join(path.read_text(encoding="utf-8") for path in
                             sorted((ROOT / "aot/port").glob("*.c")))
    hooked = {int(address, 16) for address in manifest["hooks"]}
    for i, row in enumerate(manifest["functions"]):
        path = manifest_path.parent / f"aot_funcs_{i // 40:02d}.c"
        if path not in chunks:
            chunks[path] = path.read_text(encoding="utf-8")
        address = int(row["addr"], 16)
        emitted_name = f"F_{address:08x}" + ("_orig" if address in hooked else "")
        if not re.search(rf"^void {emitted_name}\(aot_cpu \*c\)", chunks[path], re.MULTILINE):
            raise ValueError(f"AOT emitted entry missing at {address:#x} in {path}")
        if address in hooked and not re.search(
                rf"^void F_{address:08x}\(aot_cpu \*c\)", port_sources, re.MULTILINE):
            raise ValueError(f"AOT hook implementation missing at {address:#x}")

    with elf_path.open("rb") as stream:
        elf = ELFFile(stream)
        if elf.elfclass != 32 or not elf.little_endian or elf["e_machine"] != "EM_ARM":
            raise ValueError("expected little-endian ELF32 ARM")
        text = elf.get_section_by_name(".text")
        if text is None:
            raise ValueError("missing .text")
        text_start, text_end = text["sh_addr"], text["sh_addr"] + text["sh_size"]
        text_bytes = text.data()
        unwind = exidx_starts(elf)
    if text_start != int(source_summary["text"]["start"], 16) or text_end != int(source_summary["text"]["end"], 16):
        raise ValueError("ELF .text and recorded bounds differ")
    ranges = sorted((address, address + row["size"]) for address, row in by_address.items())
    gaps, end = [], text_start
    for begin, stop in ranges:
        if begin < end or begin < text_start or stop > text_end or stop <= begin:
            raise ValueError(f"invalid or overlapping function at {begin:#x}")
        if begin > end:
            gap = text_bytes[end - text_start:begin - text_start]
            gaps.append({"start": f"0x{end:x}", "end": f"0x{begin:x}",
                         "bytes": len(gap), "all_zero": all(x == 0 for x in gap)})
        end = stop
    if end < text_end:
        gap = text_bytes[end - text_start:]
        gaps.append({"start": f"0x{end:x}", "end": f"0x{text_end:x}",
                     "bytes": len(gap), "all_zero": all(x == 0 for x in gap)})
    if sum(g["bytes"] for g in gaps) != source_summary["text"]["gap_bytes"]:
        raise ValueError("uncovered text bytes differ from recorded census")

    evidence = {}
    for line in evidence_path.read_text(encoding="utf-8").splitlines():
        if line:
            item = json.loads(line)
            if item.get("binary_sha256") == binary_sha256:
                evidence[item["id"]] = item
    suites = reference_suites(differential_path, binary_sha256)
    candidates = []
    for gap in gaps:
        if not gap["all_zero"]:
            candidates.append({"address": gap["start"], "evidence": "nonzero bytes outside indexed functions",
                               "status": "unclassified code/data; inspect before promoting"})
    for address in sorted(unwind - set(by_address)):
        candidates.append({"address": f"0x{address:x}", "evidence": "unwind entry without indexed function",
                           "status": "candidate; inspect before promoting"})
    for row in indexed:
        for target in row.get("branch_targets_not_function_start", []):
            candidates.append({"address": target, "evidence": f"branch from {row['addr']}",
                               "status": "candidate; inspect before promoting"})
    records = []
    for address in sorted(by_address):
        row = by_address[address]
        i, lowered_row = lowered[address]
        unsupported = lowered_row["unsupported"]
        if not matching_names(row, lowered_row):
            raise ValueError(f"index/AOT name mismatch at {address:#x}")
        claims = [{"id": ident, "claim": evidence[ident]["claim"],
                   "confidence": evidence[ident]["confidence"]}
                  for ident in row.get("evidence", []) if ident in evidence]
        unresolved_indirect = max(0, row.get("indirect_call_sites", 0) -
                                  row.get("indirect_call_sites_resolved", 0))
        tested = suites.get(address, [])
        if unsupported:
            priority, next_action = "blocker", "Investigate each unsupported instruction; retain fatal behavior until proven."
        elif row["boundary_confidence"] == "heuristic":
            priority, next_action = "high", "Confirm inferred function extent against unwind, disassembly, and reference."
        elif unresolved_indirect:
            priority, next_action = "high", "Resolve indirect call targets and compare behavior with ARM reference."
        elif tested:
            priority, next_action = "medium", "Compare this exact AOT lowering with the tested source behavior before optimizing."
        else:
            priority, next_action = "medium", "Capture reference vectors and behavior before declaring this function working."
        records.append({
            "address": row["addr"], "name": row["name"], "demangled": row.get("demangled"),
            "aot_symbol": lowered_row["name"],
            "category": row["category"], "boundary": row["boundary_confidence"],
            "what_it_has": {"bytes": row["size"], "instructions": row.get("insn_count"),
                            "callers": len(row.get("callers", [])),
                            "callees": row.get("callees", []), "imports": row.get("imports_called", []),
                            "globals_read": row.get("globals_read", []),
                            "globals_written": row.get("globals_written", []),
                            "indirect_calls": row.get("indirect_call_sites", 0),
                            "unresolved_indirect_calls": unresolved_indirect},
            "what_it_does": {"status": "evidence-backed claim(s)" if claims else "unknown; name is only a hint",
                             "claims": claims},
            "how_it_looks_64_bit": {"form": "C lowering over 32-bit guest state, compiled for 64-bit host",
                                      "generated_entry": f"F_{address:08x}" + ("_orig" if address in hooked else ""),
                                      "host_entry": f"F_{address:08x}",
                                      "generated_file": f"aot/generated/aot_funcs_{i // 40:02d}.c",
                                      "port_hook": address in hooked,
                                      "uses_vfp": lowered_row["uses_vfp"],
                                      "unsupported_sites": unsupported},
            "is_it_working": {"AOT_emitted_without_unsupported_site": not bool(unsupported),
                              "reference_suites_with_zero_mismatch": tested,
                              "full_function_behavior": "not established by this static audit",
                              "arm64_device": "not established per function"},
            "priority": priority, "next_action": next_action,
            "source_index": "analysis/native/FUNCTION_INDEX.v7a.jsonl",
        })
    stubs = [{"address": row["addr"], "name": row["name"],
              "what_it_has": {"bytes": row["size"], "got_slot": row["got_slot"],
                              "callers": row["callers"]},
              "what_it_does": {"status": "import trampoline", "import": row["import"]},
              "how_it_looks_64_bit": "requires a resolved host import or bridge; not an AOT function",
              "is_it_working": "not established per import", "source_index": "analysis/native/FUNCTION_INDEX.v7a.jsonl"}
             for row in index if row["category"] == "plt"]
    summary = {"schema": 1, "binary_sha256": binary_sha256,
               "raw_defined_symbols": source_summary["denominators"]["raw_defined_FUNC_symbols_symtab"],
               "unique_text_functions_reviewed": len(records),
               "plt_stubs_separate": len(stubs),
               "unwind_entries": len(unwind), "unwind_matches": len(unwind & set(by_address)),
               "gaps": gaps, "candidate_starts": candidates,
               "unsupported_sites": manifest["unsupported_sites"],
               "functions_with_unsupported_sites": sum(bool(lowered[a][1]["unsupported"]) for a in by_address),
               "reference_suite_addresses": sum(bool(suites.get(a)) for a in by_address),
               "priority_counts": dict(Counter(item["priority"] for item in records)),
               "interpretation": "Static census and translation triage, not full semantic or device validation."}
    return summary, records, stubs


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--elf", required=True, type=Path, help="original ELF32 ARM library")
    parser.add_argument("--output", required=True, type=Path, help="new report directory")
    args = parser.parse_args()
    if args.output.exists():
        parser.error("output directory already exists; preserve prior reviews")
    try:
        summary, records, stubs = review(
            args.elf, ROOT / "analysis/native/FUNCTION_INDEX.v7a.jsonl",
            ROOT / "aot/generated/aot_manifest.json",
            ROOT / "analysis/native/function_summary.v7a.json",
            ROOT / "analysis/evidence/native.jsonl",
            ROOT / "tests/differential/assets/result.json")
        args.output.mkdir(parents=True)
        (args.output / "summary.json").write_text(json.dumps(summary, indent=2) + "\n")
        (args.output / "functions.jsonl").write_text(
            "".join(json.dumps(record, ensure_ascii=False) + "\n" for record in records))
        (args.output / "imports.jsonl").write_text(
            "".join(json.dumps(record, ensure_ascii=False) + "\n" for record in stubs))
    except (OSError, ValueError, KeyError) as exc:
        parser.exit(2, f"review_all: {exc}\n")
    print(f"Reviewed {len(records)} unique function starts; {len(summary['candidate_starts'])} unconfirmed candidates")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
