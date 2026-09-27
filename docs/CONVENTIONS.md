# Project conventions (shared by all work areas)

## Binary identity

Every native finding must name the binary by SHA-256. Short names used in docs:

| Short name | APK path | SHA-256 |
|---|---|---|
| `v7a` (primary reference) | `lib/armeabi-v7a/libsnailmail.so` | `e43bc913e9ba99abd2fed4d2cee40d4a33a951ecbcf8ca154d099cc8cabaa466` |
| `v5` | `lib/armeabi/libsnailmail.so` | `96dbeaeb20c60d687301ca769656727467371489db5e3ed744a93248bc8d8136` |
| APK | `original/com.sandlotgames.snailmail-1.00.apk` | `0d10908d50f2a8361d9bbd3c6c9bff025434fdfb49c0f2b97254a793fb0b29e7` |
| archive | `assets/asm.mp3` | `59740ec3a2cd1f7e9ff250e3c6128ffff922193925e3d0316db22a4118861b6a` |

Both libraries are `ET_DYN` with the first `PT_LOAD` at vaddr 0, so a
module-relative address equals the ELF virtual address. Addresses are written
as `v7a:0x15244`. Never record an absolute runtime address without the module
and its load bias.

The two builds are different compilations (different addresses and code); a
finding for one does not transfer to the other without re-verification.

## Status vocabulary (per function / per module)

`DISCOVERED` → `DISASSEMBLED` → `DECOMPILED` → `RECONSTRUCTED` → `UNIT_TESTED`
→ `REFERENCE_VALIDATED` → `ARM64_DEVICE_VALIDATED`, plus `BLOCKED`.
Statuses are a set of achieved milestones, never a single "done" flag.

* `DECOMPILED` means a decompiler produced output. It says nothing about
  correctness.
* `RECONSTRUCTED` means hand-written source exists in `reconstructed/` whose
  semantics are argued from the original instructions.
* `REFERENCE_VALIDATED` requires a comparison against the original machine code
  (e.g. the Unicorn ARM32 harness) or an original-execution capture.

## Confidence vocabulary

`established` (directly observed in bytes/instructions), `likely` (strong
static inference, not directly observed), `hypothesis` (plausible, needs
evidence). Hypotheses must be labelled as such wherever they appear.

## Evidence ledger

One JSONL file per work area in `analysis/evidence/<area>.jsonl`, one claim per
line:

```json
{"id": "EV-ASSET-0001", "claim": "...", "binary_sha256": "...",
 "location": "v7a:0x15244-0x15450 | archive bytes 0x0-0x4 | smali path:line",
 "observation": "...", "interpretation": "...", "confidence": "established",
 "contradictions": "", "next_step": ""}
```

## Function index record (`analysis/native/FUNCTION_INDEX.<binary>.jsonl`)

```json
{"binary_sha256": "...", "binary": "v7a", "addr": "0x15244", "file_offset": "0x15244",
 "size": 588, "mode": "arm", "name": "<raw symbol>", "demangled": "...",
 "alias": null, "same_address_symbols": [], "category": "game|jni|engine|libgcc|runtime|plt",
 "boundary_confidence": "symbol+exidx|symbol|heuristic", "status": ["DISCOVERED", ...],
 "evidence": [], "callers": ["0x..."], "callees": ["0x..."], "questions": []}
```

## Directory ownership

* `original/`, `work/apk_unzip/` — read-only inputs.
* `analysis/**/generated/`, `analysis/assets/extracted/` — gitignored bulk output
  (contains decompiled/extracted proprietary material); always regenerable by a
  script in `tools/`.
* `reconstructed/` — hand-written, compilable, shipping-candidate source only.
  No decompiler dumps.
* `tools/` — analysis-only helpers; never linked into the shipping APK.

## Build/test layout

Each reconstructed module `reconstructed/<area>/` provides its own
`CMakeLists.txt` defining a static library target `sm_<area>`; its tests live in
`tests/unit/<area>/CMakeLists.txt` and register with CTest. The top-level
`CMakeLists.txt` picks these up automatically.
