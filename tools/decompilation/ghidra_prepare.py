#!/usr/bin/env python3
"""Write the $d data-region list consumed by ghidra/SmPreAnalysis.java.

Usage: tools/decompilation/ghidra_prepare.py v7a|v5 OUT.txt
Each line: "0xSTART 0xEND" (half-open), for every mapping-symbol data region
inside an executable section (.plt/.text), sorted by address.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import smelf  # noqa: E402


def main():
    if len(sys.argv) != 3:
        raise SystemExit(__doc__)
    b = smelf.Binary(sys.argv[1])
    out = Path(sys.argv[2])
    out.parent.mkdir(parents=True, exist_ok=True)
    lines = [f"# {b.name} sha256={b.sha256} $d regions in executable sections"]
    n = 0
    for r in b.regions:
        if r.kind == "data":
            lines.append(f"0x{r.start:x} 0x{r.end:x}")
            n += 1
    out.write_text("\n".join(lines) + "\n")
    print(f"{out}: {n} data regions")


if __name__ == "__main__":
    main()
