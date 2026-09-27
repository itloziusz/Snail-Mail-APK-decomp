#!/usr/bin/env python3
"""Small A32 raw-code to portable C ahead-of-time recompiler prototype.

This is a teaching scaffold, separate from the Snail Mail-specific arm2c.py.
It accepts one straight-line/branching ARM-state code region and refuses any
instruction or control-flow target that it cannot represent faithfully.
"""

import argparse
from dataclasses import dataclass
from pathlib import Path
import struct


class Unsupported(Exception):
    pass


@dataclass(frozen=True)
class Instruction:
    address: int
    condition: int
    statement: str
    branch_target: int | None = None


def decode(word: int, address: int) -> Instruction:
    condition = word >> 28
    if condition not in (0, 1, 14):
        raise Unsupported(f"0x{address:08x}: condition {condition:x} is not supported")

    # BX LR is the only return form in this prototype.
    if (word & 0x0FFFFFFF) == 0x012FFF1E:
        return Instruction(address, condition, "return r[0];")

    if (word & 0x0E000000) == 0x0A000000:
        if word & 0x01000000:
            raise Unsupported(f"0x{address:08x}: BL is not supported")
        offset = word & 0x00FFFFFF
        if offset & 0x00800000:
            offset -= 0x01000000
        target = address + 8 + offset * 4
        return Instruction(address, condition, f"pc = 0x{target:08x}u; break;", target)

    if (word & 0x0C000000) != 0:
        raise Unsupported(f"0x{address:08x}: memory, coprocessor, or other instruction")

    opcode = (word >> 21) & 15
    is_immediate = (word >> 25) & 1
    set_flags = (word >> 20) & 1
    rn = (word >> 16) & 15
    rd = (word >> 12) & 15
    operand = word & 0xFFF
    if opcode not in (2, 4, 10, 13):
        raise Unsupported(f"0x{address:08x}: data-processing opcode {opcode} unsupported")
    if (opcode == 10 and (not set_flags or rd != 0)) or (opcode != 10 and set_flags):
        raise Unsupported(f"0x{address:08x}: unsupported flag update or encoding")
    if opcode == 13 and rn != 0:
        raise Unsupported(f"0x{address:08x}: malformed MOV")
    if rn == 15 and opcode != 13 or rd == 15 and opcode != 10:
        raise Unsupported(f"0x{address:08x}: PC register operand unsupported")

    if is_immediate:
        n = (operand >> 8) * 2
        imm = operand & 0xFF
        value = ((imm >> n) | (imm << (32 - n))) & 0xFFFFFFFF if n else imm
        rhs = f"0x{value:08x}u"
    else:
        if operand & 0xFF0 or (operand & 15) == 15:
            raise Unsupported(f"0x{address:08x}: shifted/PC register operand unsupported")
        rhs = f"r[{operand & 15}]"

    if opcode == 13:
        statement = f"r[{rd}] = {rhs}; pc += 4u; break;"
    elif opcode == 4:
        statement = f"r[{rd}] = r[{rn}] + {rhs}; pc += 4u; break;"
    elif opcode == 2:
        statement = f"r[{rd}] = r[{rn}] - {rhs}; pc += 4u; break;"
    else:
        statement = f"z = (r[{rn}] == {rhs}); pc += 4u; break;"
    return Instruction(address, condition, statement)


def translate(data: bytes, base: int, max_steps: int) -> str:
    if not data or len(data) % 4 or len(data) > 65536:
        raise Unsupported("input must contain 1..16384 complete A32 instructions")
    if base < 0 or base % 4 or base + len(data) > 0x100000000:
        raise Unsupported("base must be aligned and code must fit in 32-bit addresses")
    if max_steps < 1 or max_steps > 100000000:
        raise Unsupported("max-steps must be between 1 and 100000000")
    decoded = [decode(word, base + i * 4) for i, (word,) in
               enumerate(struct.iter_unpack("<I", data))]
    addresses = {item.address for item in decoded}
    for item in decoded:
        if item.branch_target is not None and item.branch_target not in addresses:
            raise Unsupported(f"0x{item.address:08x}: branch target outside code region")

    lines = [
        "/* Generated from raw little-endian ARM A32 code. Review input provenance. */",
        "#include <stdint.h>",
        "#include <stdio.h>",
        "#include <stdlib.h>",
        "#include <errno.h>",
        "typedef char require_64_bit_host[(sizeof(void *) == 8) ? 1 : -1];",
        "static uint32_t run(uint32_t a, uint32_t b, uint32_t c, uint32_t d, int *error) {",
        "  uint32_t r[16] = {a, b, c, d};",
        f"  uint32_t pc = 0x{base:08x}u;",
        "  int z = 0;",
        "  (void)r; (void)z;",
        f"  for (uint32_t step = 0; step < {max_steps}u; ++step) {{",
        "    switch (pc) {",
    ]
    for item in decoded:
        lines.append(f"      case 0x{item.address:08x}u:")
        if item.condition != 14:
            check = "z" if item.condition == 0 else "!z"
            lines.append(f"        if (!({check})) {{ pc += 4u; break; }}")
        lines.append(f"        {item.statement}")
    lines += [
        "      default: *error = 1; return 0;",
        "    }",
        "  }",
        "  *error = 2; return 0;",
        "}",
        "int main(int argc, char **argv) {",
        "  uint32_t a[4] = {0};",
        "  if (argc > 5) { fputs(\"up to four uint32 arguments\\n\", stderr); return 2; }",
        "  for (int i = 1; i < argc; ++i) {",
        "    char *end; errno = 0; unsigned long long v = strtoull(argv[i], &end, 0);",
        "    if (errno || end == argv[i] || *end || argv[i][0] == '-' || v > UINT32_MAX) {",
        "      fputs(\"invalid uint32 argument\\n\", stderr); return 2;",
        "    }",
        "    a[i - 1] = (uint32_t)v;",
        "  }",
        "  int error = 0; uint32_t result = run(a[0], a[1], a[2], a[3], &error);",
        "  if (error) { fprintf(stderr, \"guest stopped: %s\\n\",",
        "                       error == 1 ? \"PC left translated code\" : \"step limit\"); return 1; }",
        "  printf(\"%u\\n\", result); return 0;",
        "}",
    ]
    return "\n".join(lines) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("input", type=Path, help="little-endian A32 raw code region")
    parser.add_argument("--output", required=True, type=Path, help="generated C source")
    parser.add_argument("--base", type=lambda value: int(value, 0), default=0x1000)
    parser.add_argument("--max-steps", type=int, default=1000000)
    args = parser.parse_args()
    try:
        source = translate(args.input.read_bytes(), args.base, args.max_steps)
        args.output.write_text(source, encoding="utf-8")
    except (Unsupported, OSError) as exc:
        parser.exit(2, f"recompile: {exc}\n")
    print(f"Wrote {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
