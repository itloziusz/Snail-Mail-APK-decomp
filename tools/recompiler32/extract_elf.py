#!/usr/bin/env python3
"""Extract one named ARM-state function from a little-endian ELF32 ARM image.

Requires pyelftools. This is a conservative staging helper, not an ELF loader:
it refuses ambiguous names, Thumb, relocations within the selected function,
and functions without file-backed executable bytes.
"""

import argparse
import hashlib
import json
from pathlib import Path

from elftools.common.exceptions import ELFError
from elftools.elf.elffile import ELFFile
from elftools.elf.relocation import RelocationSection


class ExtractionError(Exception):
    pass


def functions(elf: ELFFile) -> list[tuple[str, int, int, int]]:
    found = set()
    for table_name in (".symtab", ".dynsym"):
        table = elf.get_section_by_name(table_name)
        if table is None:
            continue
        for symbol in table.iter_symbols():
            if symbol["st_info"]["type"] != "STT_FUNC":
                continue
            section = symbol["st_shndx"]
            if type(section) is not int or not symbol.name:
                continue
            found.add((symbol.name, symbol["st_value"], symbol["st_size"], section))
    return sorted(found)


def extract(path: Path, name: str) -> tuple[bytes, dict]:
    with path.open("rb") as stream:
        elf = ELFFile(stream)
        if elf.elfclass != 32 or not elf.little_endian or elf["e_machine"] != "EM_ARM":
            raise ExtractionError("expected little-endian ELF32 ARM")
        if elf["e_type"] not in ("ET_DYN", "ET_EXEC"):
            raise ExtractionError("only ET_DYN and ET_EXEC images are supported")
        matches = [item for item in functions(elf) if item[0] == name]
        if len(matches) != 1:
            raise ExtractionError(f"expected one unambiguous function named {name!r}; found {len(matches)}")
        _, address, size, section_index = matches[0]
        if address & 1:
            raise ExtractionError("Thumb function; this starter accepts only A32")
        if not size or size % 4 or size > 65536 or address % 4:
            raise ExtractionError("function must contain 1..16384 aligned A32 words")
        if section_index >= elf.num_sections():
            raise ExtractionError("symbol references a missing section")
        section = elf.get_section(section_index)
        if section["sh_type"] != "SHT_PROGBITS" or not section["sh_flags"] & 0x4:
            raise ExtractionError("function is not in a file-backed executable section")
        begin = address - section["sh_addr"]
        if begin < 0 or begin + size > section["sh_size"]:
            raise ExtractionError("function extends outside its executable section")
        if address + size > 0x100000000:
            raise ExtractionError("function exceeds 32-bit address space")
        for rel_section in elf.iter_sections():
            if isinstance(rel_section, RelocationSection):
                for relocation in rel_section.iter_relocations():
                    if address <= relocation["r_offset"] < address + size:
                        raise ExtractionError("relocation inside code: handle it before translation")
        table = elf.get_section_by_name(".symtab")
        if table is not None:
            for symbol in table.iter_symbols():
                if symbol.name.startswith(("$d", "$t")) and address <= symbol["st_value"] < address + size:
                    raise ExtractionError("data or Thumb mapping symbol inside function")

        section_name = section.name
        code = section.data()[begin:begin + size]
    raw = path.read_bytes()
    manifest = {"schema": 1, "source": str(path), "source_sha256": hashlib.sha256(raw).hexdigest(),
                "symbol": name, "section": section_name, "base": f"0x{address:08x}",
                "size": size, "code_sha256": hashlib.sha256(code).hexdigest(),
                "recompile_command": ["python3", "tools/recompiler32/recompile.py",
                                      "<extracted-file>", "--base", f"0x{address:08x}",
                                      "--output", "<generated.c>"]}
    return code, manifest


def list_functions(path: Path) -> list[dict]:
    with path.open("rb") as stream:
        elf = ELFFile(stream)
        if elf.elfclass != 32 or not elf.little_endian or elf["e_machine"] != "EM_ARM":
            raise ExtractionError("expected little-endian ELF32 ARM")
        return [{"name": name, "address": f"0x{address:08x}", "bytes": size,
                 "mode": "Thumb" if address & 1 else "ARM"}
                for name, address, size, _ in functions(elf)]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("elf", type=Path)
    action = parser.add_mutually_exclusive_group(required=True)
    action.add_argument("--list", action="store_true", help="list named functions as JSON")
    action.add_argument("--symbol", help="exact function name to extract")
    parser.add_argument("--output", type=Path, help="output raw A32 file")
    parser.add_argument("--manifest", type=Path, help="output provenance JSON")
    args = parser.parse_args()
    if args.symbol and not args.output:
        parser.error("--symbol requires --output")
    try:
        if args.list:
            print(json.dumps(list_functions(args.elf), indent=2))
        else:
            code, manifest = extract(args.elf, args.symbol)
            args.output.write_bytes(code)
            if args.manifest:
                args.manifest.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
            print(f"Wrote {len(code)} bytes from {args.symbol} at {manifest['base']}")
    except (OSError, ExtractionError, ELFError, ValueError, IndexError) as exc:
        parser.exit(2, f"extract_elf: {exc}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
