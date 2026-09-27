# ARM32 function review ledger

This report audits the original Snail Mail ARM32 library identified by SHA-256
`e43bc913e9ba99abd2fed4d2cee40d4a33a951ecbcf8ca154d099cc8cabaa466`.
It is a **static census and work queue**, not a claim that every function's
behavior has been understood or validated on an ARM64 device.

| Measure | Result | Meaning |
| --- | ---: | --- |
| Defined `FUNC` symbol entries | 1,183 | Includes same-address aliases |
| Unique `.text` function starts | 1,173 | One review record for each start |
| PLT import stubs | 94 | Separate import ledger, not game functions |
| ARM unwind entries | 1,151 | All point to indexed starts |
| Uncovered `.text` | 8 bytes | Two four-byte, all-zero alignment gaps |
| Additional supported starts | 0 | No independent evidence to promote another address |
| AOT functions with unsupported sites | 8 | 44 fatal sites in libgcc unwinder routines |
| Exact addresses in zero-mismatch reference suites | 13 | Limited test cases, not full function proof |

The Ghidra analysis also reported 92 functions without ordinary symbols,
all within the separately counted PLT/external area. A branch target inside
an existing function, a name in a symbol table, or a plausible instruction
sequence alone does not justify inventing another function start. If a later
build has a new address, the checker leaves it as a candidate until boundaries
and behavior are corroborated.

The [summary](../analysis/native/RECOMPILER_REVIEW.summary.json),
[function ledger](../analysis/native/RECOMPILER_REVIEW.functions.jsonl), and
[import ledger](../analysis/native/RECOMPILER_REVIEW.imports.jsonl) are
machine-readable. Each function record answers the requested sequence:

1. **What it has:** size, instruction count, direct callers/callees, imports,
   global reads/writes, and unresolved indirect calls from the static index.
2. **What it does:** only evidence-backed behavior claims are included; an
   otherwise suggestive symbol name remains an unverified hint.
3. **64-bit shape:** the generated C entry and chunk, guest 32-bit state,
   VFP use, and unsupported sites. This is 32-bit guest logic in a 64-bit
   host process, not a rewrite to 64-bit game pointers.
4. **Does it work:** reports emitted code, exact-address reference suites,
   and the outstanding whole-function and ARM64-device validation separately.
5. **Next action:** unsupported instructions, inferred boundaries and
   unresolved indirect calls take priority over optimization.

The 94 import records identify their GOT slots and imported names. Their
actual host bridge behavior still needs separate testing. The current ledger
has 8 blockers, 37 high-priority functions, and 1,128 medium-priority
functions. No per-function record asserts complete correctness. In particular,
13 exact-address suite associations are narrow tests of original routines;
they do not establish full equivalence of generated AOT code.

## Regenerate and extend the review

After the [private original build setup](BUILD_FROM_ORIGINAL.md), regenerate
the AOT manifest from your own original APK and run:

```sh
python3 tools/aot/arm2c.py \
  --elf work/apk_unzip/lib/armeabi-v7a/libsnailmail.so --out aot/generated
python3 tools/recompiler32/review_all.py \
  --elf work/apk_unzip/lib/armeabi-v7a/libsnailmail.so \
  --output /tmp/snail-review-new
```

The tool checks the ELF SHA across the index, census, AOT manifest, and
differential reference; checks AOT/index address equality and symbol aliases;
checks `.text` extents, overlaps, zero gaps, and unwind entries; and writes
`summary.json`, `functions.jsonl`, `imports.jsonl` into a new directory. It
refuses to overwrite an existing review. Compare regenerated output against
the committed ledgers before changing claims.

For each priority record, inspect the referenced A32 instructions and calls,
write a concrete behavior hypothesis and reference vectors, compare the
generated AOT path against the original, then change the translator with a
reproducer and rerun native and device tests. If evidence disagrees, keep the
status unresolved and investigate the instruction, ABI, data, or import bridge.
The small [editable Visual Studio exporter](RECOMPILER_STARTER.md#edit-the-translation-in-visual-studio)
is a separate teaching subset; it cannot import this entire game-specific
AOT translation into an MSVC project yet.
