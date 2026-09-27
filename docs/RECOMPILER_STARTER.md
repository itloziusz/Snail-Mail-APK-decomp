# Experimental ARM32 recompiler starter

`tools/recompiler32/recompile.py` is a small, independent ahead-of-time (AOT)
translator for **raw, little-endian ARM A32 code**, producing portable C99.
Compile that C with a 64-bit host compiler to run the translated function as a
64-bit process. This demonstrates the decode → validate → lower → compile
workflow. It is a developer starter, not a one-click converter for arbitrary
32-bit games, ELF libraries, APKs, or Windows executables.

The actual Snail Mail port uses the larger, game-specific
[`tools/aot/arm2c.py`](../tools/aot/arm2c.py) with
[`aot/runtime/`](../aot/runtime/) and a reconstructed Android/JNI/graphics
boundary. Study those after the minimal starter.

## Helper workflow

| Helper | What it does |
| --- | --- |
| [`extract_elf.py`](../tools/recompiler32/extract_elf.py) | Lists ELF32 ARM functions; extracts one named ARM-state function and writes a SHA-256 provenance manifest |
| [`analyze.py`](../tools/recompiler32/analyze.py) | Counts accepted and rejected A32 words, records unsupported reasons and branch examples in JSON |
| [`recompile.py`](../tools/recompiler32/recompile.py) | Rejects unsupported input and generates C99 for the accepted region |
| [`verify.py`](../tools/recompiler32/verify.py) | Compiles generated C and checks `r0` results against JSON test vectors |

For an ELF32 ARM shared object you are authorized to examine, install the
existing `pyelftools==0.33` Python dependency, then choose an exact symbol:

```sh
python3 tools/recompiler32/extract_elf.py path/to/game.so --list > /tmp/functions.json
python3 tools/recompiler32/extract_elf.py path/to/game.so \
  --symbol chosen_function --output /tmp/chosen.a32 --manifest /tmp/chosen.json
```

Use the `base` address in `/tmp/chosen.json` for every following command. For
example, if it says `0x00001000`:

```sh
python3 tools/recompiler32/analyze.py /tmp/chosen.a32 \
  --base 0x1000 --strict --output /tmp/coverage.json
python3 tools/recompiler32/recompile.py /tmp/chosen.a32 \
  --base 0x1000 --output /tmp/chosen.c
python3 tools/recompiler32/verify.py /tmp/chosen.a32 path/to/cases.json \
  --base 0x1000
```

`--strict` exits with code 2 if the function is too large or contains any
unsupported word; the JSON report still records reasons. `verify.py` exits
with code 1 on a result mismatch and 2 for invalid input or build errors.
Each case has up to four `uint32` guest arguments and one expected `r0`:

```json
{"cases":[{"args":[0,5],"expect":5}]}
```

See [`examples/count_cases.json`](../tools/recompiler32/examples/count_cases.json)
for a runnable set with the example below. Expected outputs must come from a
trusted reference or a manually checked function contract; this helper is not
an ARM emulator and cannot establish correctness by itself. The ELF helper
only stages a named function from a little-endian ELF32 ARM `ET_DYN` or
`ET_EXEC` image. It refuses Thumb symbols, ambiguous names, internal mapping
symbols for data/Thumb, code relocations, and missing executable bytes. It
does not load or link the game.

## Run the example

Python 3.10+ and a C99 compiler are sufficient. From the repository root,
create a tiny A32 counting function. The word sequence is `MOV r0,#0`,
`ADD r0,r0,#1`, `CMP r0,r1`, `BNE` back to `ADD`, then `BX LR`.

```sh
python3 -c 'import struct; open("/tmp/count.a32","wb").write(struct.pack("<5I",0xE3A00000,0xE2800001,0xE1500001,0x1AFFFFFC,0xE12FFF1E))'
python3 tools/recompiler32/recompile.py /tmp/count.a32 --base 0x1000 --output /tmp/count.c
cc -std=c99 -Wall -Wextra -Werror -O2 /tmp/count.c -o /tmp/count64
/tmp/count64 0 5
python3 tools/recompiler32/analyze.py /tmp/count.a32 --base 0x1000 --strict
python3 tools/recompiler32/verify.py /tmp/count.a32 \
  tools/recompiler32/examples/count_cases.json --base 0x1000
```

The output is `5`. The generated program accepts up to four unsigned 32-bit
arguments as guest `r0` through `r3`, prints the returned `r0`, and reports
an error if the guest leaves the translated region or exceeds the step limit.
The default limit is one million instructions; change it with `--max-steps`.
The C source can be compiled on x86-64 or AArch64; this tool does not produce
an APK or link Android interfaces.

## Translation contract

| Accepted | Current rule |
| --- | --- |
| Input | One raw A32 region, 4-byte aligned base, 1 to 16,384 little-endian words |
| Instructions | `MOV`, `ADD`, `SUB` without flag updates; `CMP` setting Z; `B`; `BX LR` |
| Operand 2 | Rotated immediate or unshifted `r0`–`r14` |
| Predication | Always, EQ, or NE; other ARM conditions fail at translation |
| Branches | Direct `B` targets must land inside the input region |
| Arithmetic | Unsigned 32-bit wrap in `uint32_t` guest registers |
| Return | `BX LR` returns `r0` to the 64-bit host program |

Unsupported instructions, malformed encodings, out-of-range branches, and
truncated input fail before the output C is written. It deliberately refuses
memory instructions, Thumb, VFP/NEON, calls, indirect branches, syscalls,
relocations, dynamic libraries, rendering, audio, saves, and platform APIs.
There is no claim of general game compatibility or semantic parity beyond the
listed subset. Run untrusted generated programs with ordinary process isolation.

## Extend it for another game

1. Extend the ELF symbol extractor into a loader, and add PE support if needed.
   Identify executable ranges, relocation records, symbols, and mode changes.
   Preserve provenance and reject ambiguous code.
2. Expand instruction decoding and differential tests against an ARM reference
   for flags, shifts, memory, Thumb, floating point, and exceptions. Preserve
   32-bit address semantics on a 64-bit host.
3. Add a bounded guest memory model and a verified dispatch table for calls
   and indirect branches; never guess a control-flow target.
4. Implement a bridge for the game's OS, graphics, input, audio, and save APIs.
   A 64-bit process alone does not replace 32-bit libraries or Android JNI.
5. Compile with the target toolchain, package separately, and compare observed
   gameplay and assets against the original on real hardware.

Run its end-to-end tests with:

```sh
python3 -m unittest discover -s tools/recompiler32 -p 'test_*.py' -v
```

These compile and execute generated C for a loop, arithmetic wrap, input
rejection, and the step guard. They also exercise the ELF → analysis →
generated C → vector comparison path and verify failure reporting. The Snail
Mail-specific translator's separate
testing and limitations are described in [testing](TESTING.md) and
[porting status](PORTING_STATUS.md).
