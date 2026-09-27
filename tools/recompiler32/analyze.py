#!/usr/bin/env python3
"""Inspect a raw A32 region for the starter translator's exact coverage."""

import argparse
from collections import Counter
import hashlib
import json
from pathlib import Path
import struct

from recompile import Unsupported, decode


def inspect(data: bytes, base: int, max_examples: int = 20) -> dict:
    if not data or len(data) % 4 or len(data) > 16 * 1024 * 1024:
        raise Unsupported("input must contain complete A32 words (up to 16 MiB)")
    if base < 0 or base % 4 or base + len(data) > 0x100000000:
        raise Unsupported("base must be aligned and input must fit in 32-bit addresses")
    if max_examples < 0 or max_examples > 1000:
        raise Unsupported("max-examples must be between 0 and 1000")

    issues, branches, reasons = [], [], Counter()
    supported = 0
    for i, (word,) in enumerate(struct.iter_unpack("<I", data)):
        address = base + i * 4
        try:
            item = decode(word, address)
            if item.branch_target is not None and not (
                    base <= item.branch_target < base + len(data)):
                raise Unsupported(f"0x{address:08x}: branch target outside code region")
        except Unsupported as exc:
            reason = str(exc).split(": ", 1)[-1]
            reasons[reason] += 1
            if len(issues) < max_examples:
                issues.append({"address": f"0x{address:08x}", "word": f"0x{word:08x}",
                               "reason": reason})
            continue
        supported += 1
        if item.branch_target is not None and len(branches) < max_examples:
            branches.append({"from": f"0x{address:08x}",
                             "to": f"0x{item.branch_target:08x}"})

    return {"schema": 1, "base": f"0x{base:08x}", "sha256": hashlib.sha256(data).hexdigest(),
            "bytes": len(data), "instructions": len(data) // 4, "supported": supported,
            "unsupported": len(data) // 4 - supported, "reasons": dict(sorted(reasons.items())),
            "issue_examples": issues, "branch_examples": branches,
            "examples_limited_to": max_examples,
            "ready_for_starter": supported == len(data) // 4 and len(data) <= 65536}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("input", type=Path)
    parser.add_argument("--base", type=lambda value: int(value, 0), default=0x1000)
    parser.add_argument("--max-examples", type=int, default=20)
    parser.add_argument("--output", type=Path, help="write JSON here instead of stdout")
    parser.add_argument("--strict", action="store_true", help="exit 2 unless ready for starter")
    args = parser.parse_args()
    try:
        result = inspect(args.input.read_bytes(), args.base, args.max_examples)
        output = json.dumps(result, indent=2) + "\n"
        if args.output:
            args.output.write_text(output, encoding="utf-8")
        else:
            print(output, end="")
    except (Unsupported, OSError) as exc:
        parser.exit(2, f"inspect: {exc}\n")
    return 2 if args.strict and not result["ready_for_starter"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
