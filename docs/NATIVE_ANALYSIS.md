# Native analysis pipeline (libsnailmail.so)

This document covers how the two native libraries are inventoried, disassembled and decompiled, and what the resulting numbers mean. The boot trace built on this pipeline is in `docs/BOOT_CHAIN.md`, and the claims are recorded in `analysis/evidence/native.jsonl` (`EV-NAT-*`).

| short | APK path | SHA-256 | role |
|---|---|---|---|
| `v7a` | `lib/armeabi-v7a/libsnailmail.so` | `e43bc913e9ba99abd2fed4d2cee40d4a33a951ecbcf8ca154d099cc8cabaa466` | primary reference |
| `v5` | `lib/armeabi/libsnailmail.so` | `96dbeaeb20c60d687301ca769656727467371489db5e3ed744a93248bc8d8136` | cross-check build |

Every script verifies these hashes before it reads anything, and reads the inputs read-only from `work/apk_unzip/`. Addresses are ELF vaddrs, which equal module-relative addresses because the first `PT_LOAD` is at 0.

## 1. Pipeline and commands

Run from the repo root. Everything is deterministic: two consecutive runs of the Python stages produced byte-identical outputs. The Ghidra stage is configuration-pinned.

```sh
# 1. ELF audit  (~9 s)
python3 tools/decompilation/elf_audit.py --all                 # -> analysis/native/elf_audit.{v7a,v5}.json

# 2. Ghidra (optional before 3, but 3 merges its DECOMPILED status) (~2 min per binary)
MAXMEM=6G tools/decompilation/run_ghidra.sh v7a all            # analyze + export
MAXMEM=6G tools/decompilation/run_ghidra.sh v5  all
#   -> work/ghidra/SnailMail_<bin>.gpr/.rep (project), work/ghidra/<bin>_*.log
#   -> analysis/native/generated/decomp/<bin>/<addr8>_<name>.c   (gitignored)
#   -> analysis/native/generated/<bin>_callgraph.json            (gitignored)
#   -> analysis/native/strings_xrefs.<bin>.json
#   -> analysis/native/ghidra_export_summary.<bin>.json

# 3. Function index, xrefs, summaries  (~10 s)
python3 tools/decompilation/function_index.py --all
#   -> analysis/native/FUNCTION_INDEX.{v7a,v5}.jsonl, function_summary{,.v7a,.v5}.json, xrefs.{v7a,v5}.json

# 4. Classes / vtables / globals (reads xrefs for vtable materialisation sites)
python3 tools/decompilation/classes.py v7a && python3 tools/decompilation/classes.py v5

# 5. Call-graph cross-check capstone vs Ghidra
python3 tools/decompilation/compare_callgraphs.py v7a && python3 tools/decompilation/compare_callgraphs.py v5
```

`run_ghidra.sh <bin> analyze|export|all` expands to the following:

```sh
python3 tools/decompilation/ghidra_prepare.py <bin> work/ghidra/<bin>_data_regions.txt
$GHIDRA/support/analyzeHeadless work/ghidra SnailMail_<bin> \
   -import <lib> -processor ARM:LE:32:v7|ARM:LE:32:v5t -cspec default \
   -loader ElfLoader -loader-imagebase 0 \
   -scriptPath tools/decompilation/ghidra \
   -preScript SmPreAnalysis.java work/ghidra/<bin>_data_regions.txt \
   -max-cpu 3 -analysisTimeoutPerFile 7200 -log ... -scriptlog ...
$GHIDRA/support/analyzeHeadless work/ghidra SnailMail_<bin> -process libsnailmail.so -noanalysis -readOnly \
   -scriptPath tools/decompilation/ghidra \
   -postScript SmExport.java <bin> <sha256> analysis/native 60 -max-cpu 3 -log ... -scriptlog ...
```

### Tool versions (as run on 2026-09-27)

* Ghidra 11.4.2 PUBLIC, running on OpenJDK 21.0.10. The scripts are Java `GhidraScript`s, so they need neither Jython nor PyGhidra; `Ghidra/Features/Jython` and `PyGhidra` exist but are unused.
* Python 3.11.15
* capstone 5.0.9 (core 5.0)
* pyelftools 0.33
* lief 1.0.0 (unused)
* GNU c++filt 2.42 (binutils), used as the demangler
* llvm-objdump/llvm-readelf 18.1.3, used for manual verification only

## 2. Stage details

### 2.1 `smelf.py` (shared)

This module parses:

* sections and segments
* `.symtab` and `.dynsym`
* `.rel.dyn` and `.rel.plt`, resolved against `.dynsym`
* `.ARM.exidx` (prel31 decoding; CANTUNWIND, inline and extab kinds)
* mapping symbols, turned into regions per executable section
* the classic ARM PLT: 20-byte header plus 12-byte `add ip,pc / add ip,ip / ldr pc,[ip]!` stubs, decoded to GOT slot → `JUMP_SLOT` symbol

`resolve_word(va)` returns the value of a data word after dynamic relocation at load bias 0. The rules are:

* `RELATIVE`: the stored addend
* `ABS32`: S+A
* `GLOB_DAT` and `JUMP_SLOT`: S
* undefined symbols: `import`

### 2.2 `armdis.py`: disassembly and constant propagation

Capstone decodes **only** bytes covered by `$a` (or `$t`) mapping regions. `$d` literal pools and tables are never decoded as code. If an invalid decode ever occurs, it is recorded and skipped; there are 0 in both builds.

The per-function `Resolver` is a forward dataflow analysis over basic blocks. At each merge it keeps only the register and stack-slot values that agree on every path. It models:

* `ldr rX,[pc,#d]` literal loads, using the relocated word
* `add/sub rX, pc|GOT, …` (PC-relative and GOTOFF addresses)
* `ldr rX,[GOT, rI]` (GOT entry → symbol address)
* `movw/movt/mov/mvn` immediates
* stack slots relative to the entry `sp` (push/pop/str/ldr/ldrd/strd/ldm/stm), because big functions spill the GOT base to the stack

Calls clobber `r0-r3, r12, lr`, and any frame slot at or above a frame pointer passed as an argument. A conditional return leaves the fall-through state unchanged.

Every load or store through a known address is recorded as a `read`/`write` ref, and every materialised address as an `addr` ref. Each ref keeps its instruction address. This is **static inference**, independent of Ghidra, and the two are compared in stage 5.

Control flow is classified as:

* `BL`, and `BLX #imm`: direct call
* `B` to another symbol: tail call
* `BLX reg`, or `mov lr,pc` followed by a PC write: indirect call. GCC 4.4 emitted the pre-v5 `mov lr,pc; ldr pc,[..]` idiom even in the v7 build, and it covers 75 of the 78 indirect call sites.
* `bx lr`, `pop {…,pc}`, `ldm sp!,{…,pc}`: return
* `addls pc,pc,rX,lsl #2` followed by a table of `B`, or `ldr pc,[pc,rX,lsl #2]` followed by a `$d` table: switch
* anything else writing PC: unresolved indirect jump. There are none in game code.

### 2.3 `function_index.py`

It writes one record per **unique** function start address. It merges same-address aliases (`same_address_symbols`) and adds the 94 PLT stubs as separate records with `category: plt`. The primary alias is chosen deterministically by these keys, in order:

1. non-zero size
2. binding, GLOBAL before WEAK before LOCAL
3. fewer leading underscores
4. lexicographic order

Record fields: the `docs/CONVENTIONS.md` schema plus

* `size_source`, `exidx`, `component_hint`, `insn_count`
* `tailcallees`, `imports_called`, `indirect_call_sites(_resolved)`, `indirect_jump_sites`, `switch_tables`
* `globals_read`, `globals_written`, `addresses_taken`, `address_taken_by`
* `branch_targets_not_function_start`

**Category rules:**

* `jni`: the raw name starts with `Java_`, meaning a JNI-exported native method.
* `runtime`: libgcc or ARM EABI helpers, matched by `RUNTIME_RE` in `function_index.py`:
  * `__aeabi_*`, `__{u,}{div,mod}{si,di}3`, `__div0`
  * `__gnu_*`, `_Unwind_*`, `___Unwind_*`
  * soft-float helpers `__{add,sub,mul,div}{sf,df}3`, `__{cmp,eq,…}{sf,df}2`, `__fix*`, `__float*`, `__extendsfdf2`, `__truncdfsf2`
  * the static unwinder helpers `restore_core_regs`, `search_EIT_table`, `get_eit_entry`, `unwind_phase2*`, `next_unwind_byte`, `selfrel_offset31`, `unwind_UCB_from_context`
  * any locally defined `__cxa_*`

  As a sanity rule, every runtime function must lie in the contiguous libgcc tail of `.text`. That tail starts at `v7a:0x7ee30` and `v5:0x87258`. The script reports runtime functions found outside the tail, and tail functions that do not match the regex; both lists are empty.
* `plt`: a decoded PLT stub. It is not a symbol.
* `game`: every other defined FUNC symbol, meaning application C/C++ code. This includes JNI-header inlines (`_JNIEnv::Call*Method`) and the `JAVA*` bridge functions, which the component hint distinguishes.

`component_hint` is a secondary, heuristic, name-based label (`COMPONENT_RULES`). Its values include `engine_class_cR`, `rshell_platform`, `pfm_platform`, `java_bridge`, `openfeint`, `resource_archive` and `object_proc`. It carries no evidential weight.

**`boundary_confidence`** takes these values:

* `symbol+exidx`: the symbol has a size, an EXIDX entry starts exactly at the function, and the next EXIDX start is at or after `addr+size`.
* `symbol`: the symbol has a size but EXIDX is absent or disagrees.
* `heuristic`: every alias has `st_size` 0, so the size is taken as the distance to the next FUNC symbol.

**`status`** takes these values:

* `DISCOVERED`: a symbol exists.
* `DISASSEMBLED`: the whole range is covered by mapping regions and capstone decoded every `$a` byte without error.
* `DECOMPILED`: Ghidra produced C without error. This is merged from `ghidra_export_summary.<bin>.json`. It says **nothing** about correctness.

`callers` and `callees` come from direct `BL`/`BLX #imm` targets. `tailcallees` come from `B` to another function start. Branches internal to the function are excluded.

### 2.4 Ghidra configuration and why

| setting | reason |
|---|---|
| `ARM:LE:32:v7` (v7a), `ARM:LE:32:v5t` (v5), compiler spec `default` | These match `Tag_CPU_arch`. There is no `$t` code, so `SmPreAnalysis.java` forces `TMode=0` over executable blocks. |
| `-loader-imagebase 0` | Ghidra's default ARM ET_DYN base is 0x10000, which would shift every address. With base 0, Ghidra addresses equal ELF vaddrs. |
| `SmPreAnalysis.java`: `$d` regions → dwords/bytes before auto-analysis | Literal pools and inline tables are never disassembled: 780 regions (4683 dwords) in v7a, 660 regions in v5. |
| `SmExport.java`: every v7a function is re-applied with `__stdcall_softfp` | ARM.cspec's default prototype is hard-float (floats in `s0-s15`), but v7a is softfp (`Tag_ABI_HardFP_use: deprecated`, no `Tag_ABI_VFP_args`). Functions with a demangler-applied signature are re-applied with every parameter explicit, so the `this` of `__thiscall` methods survives. v5 uses `ARM_v45.cspec`, which is already integer-only. |
| `SmExport.java`: operand references into `[0, first exec block)` are removed (in memory; v7a 4047, v5 4570), and the decompiler option "Analysis.Infer constant pointers" is off | With base 0, small integers such as GL enums, sizes and the µs budget alias addresses in `.hash/.dynsym/.dynstr`. The decompiler then printed `glBindBuffer("UnInit", …)` instead of `glBindBuffer(0x8893, …)`. |
| per-function decompile timeout 60 s | There were no timeouts. |
| `-readOnly` export | The saved project always holds the pure auto-analysis result. All fixups are recomputed on every export. |

## 3. Coverage (denominators explicit)

| quantity | v7a | v5 |
|---|---|---|
| raw defined `STT_FUNC` in `.symtab` (includes aliases and libgcc) | 1183 | 1253 |
| … of which in `.dynsym` (exported) | 1158 | 1228 |
| **unique function start addresses** (denominator for everything below) | **1173** | **1214** |
| same-address alias groups | 9 | 36 |
| zero-size symbol addresses (libgcc asm) | 11 | 12 |
| category `game` / `jni` / `runtime` | 1096 / 18 / 59 | 1096 / 18 / 100 |
| PLT stubs (separate records, not in the 1173/1214) | 94 | 94 |
| functions with an EXIDX entry (EXIDX entries total) | 1151 (1151) | 1151 (1151) |
| boundary `symbol+exidx` / `symbol` / `heuristic` | 1151 / 11 / 11 | 1151 / 51 / 12 |
| status DISASSEMBLED / DECOMPILED | 1173 / 1173 | 1214 / 1214 |
| `.text` bytes / covered by function symbol ranges | 447,124 / 447,116 | 481,984 / 481,984 |
| `.text` gaps (all zero padding in `$d`) | 2 × 4 B (`0x19694`, `0x4b3fc`) | 0 |
| ARM instructions decoded ($a only) / invalid | 107,385 / 0 | 116,938 / 0 |
| direct call edges / tail-call edges (unique; v5 is higher because of soft-float helper calls) | 2,483 / 285 | 4,005 / 286 |
| indirect call sites / statically resolved | 78 / 0 (see BOOT_CHAIN §6) | 78 / 0 |
| switch tables (all resolved) | 46 | 46 |
| Ghidra functions: total / decompiled / failed / timeouts | 1360 / 1265 / 0 / 0 (95 EXTERNAL placeholders skipped) | 1402 / 1307 / 0 / 0 |
| Ghidra functions without an ELF symbol, outside `.plt` | 0 | 0 |
| capstone vs Ghidra call edges: common / only-capstone / only-Ghidra | 3096 / 3 / 0 | 4618 / 3 / 3 |
| strings defined by Ghidra / referenced | 4173 / 814 | 4323 / 839 |
| C++ nested-name scopes / proven classes / vtables / typeinfo | 139 / 52 / 42 / 0 | same |
| `OBJECT` symbols / `.bss` bytes covered (of 3,275,740) | 254 / 3,275,691 | same |

`callgraph_crosscheck.<bin>.json` lists every edge on which the two tools differ. For v7a there are 3 edges that only capstone found. Each one is a tail branch in which the calling function is a single tail branch, or ends in one, and Ghidra models it as a thunk. For v5 the differences are fall-throughs between adjacent hand-written soft-float routines.

**Interpretation.** The symbol table is complete for game code. Ghidra found no function in `.text` without a symbol, and the only uncovered bytes are 8 bytes of zero padding. The raw FUNC count (1183) must not be quoted as "recovered game functions": the game-code count is **1096** (plus 18 JNI entry points) in both builds.

## 4. Key binary facts

* **ISA.** Both builds are ARM-state only: no `$t` symbols, no odd FUNC values, no `BLX #imm` (EV-NAT-0003).
  * v7a uses v6T2+ `movw/movt` (6296) and VFPv2 (24k instructions, softfp). Double-precision VFP is rare: 57 instructions, mostly `vcvt.f64.f32`/`vcvt.f32.f64`, plus 3 `vsqrt.f64`.
  * v5 is soft-float v5TE. Its VFP opcodes appear only in the libgcc unwinder.
  * Capstone's `v6` group on `mul`/`mla` is an LLVM encoding-predicate artefact, not a v6 requirement.
* **Linking.** The libraries have no `JNI_OnLoad`, no `dlopen`/`dlsym`, no `DT_INIT`/`DT_FINI`/`FINI_ARRAY` and no `__cxa_atexit`. `liblog` is NEEDED but unused, because `wprintf` is a no-op. `DT_TEXTREL` covers only 12 libgcc-unwinder relocations. The 11 `INIT_ARRAY` constructors are all `_GLOBAL__I_<file>.cpp`, which gives the translation-unit names Ad, RMaths, RShell, RObject, Font, RSprite, Game, SubGame, Voice, LoadingBar and GL.
* **C++.** RTTI is absent (`-fno-rtti`). There are 42 twelve-byte vtables with a single virtual slot: `AI()`, or `LevelInit(int)` for `cRGame`. There are no virtual destructors and no `operator delete` import. `__cxa_guard_*` is used once, in `tVector::Cross`.
* **Data.** `.bss` is 3.2 MB:
  * `gGroup` and `gGroup0` take 1 MiB each
  * `RShellMemory`, `gRSpriteManager` and `gResourceManager` take about 170 KB each
  * the sin/cos tables take 128 KB each

  The 3.8 MB `cRGame` object is allocated on the heap in `AppInit`.
* **Indirect control flow is enumerable** (EV-NAT-0024). It consists of:
  * the 42 vtable slots
  * the 11 INIT_ARRAY entries
  * 3 GOT-held function pointers, for the `cRHash` callbacks and a qsort comparator
  * JNIEnv calls

## 5. Known limitations

* The resolver does not track heap object fields or pointer aliasing. Accesses such as `*(Game+0x718b4)` show up as a read of `Game` only, and field-level layouts need Ghidra or manual work. `globals_read` and `globals_written` are **lower bounds**.
* 194 of 747 v7a functions (103 of 655 in v5) have literal-pool loads but no resolved symbol refs. The ones checked, such as `tMatrix::*`, `tColour::Red` and `Tan`, load float constants, so this is expected. The remaining ones have not been audited individually.
* Ghidra pseudocode artefacts remain:
  * 64-bit arithmetic appears as `CONCAT44`
  * some JNI and OSD argument lists are wrong
  * the `in_fpscr` / `VectorSignedToFloat` noise comes from VFP conversions

  Every claim in `BOOT_CHAIN.md` was verified against the disassembly.
* Ghidra's `strings_xrefs` covers only strings Ghidra defined, and references it created. `xrefs.<bin>.json` has an independent set of string refs from the capstone resolver.
* The per-function `DECOMPILED` status is binary. There is no semantic validation yet, because the Unicorn reference harness does not exist yet.
* The v5 decompilation and index were produced, but the boot trace was done on v7a only.
* The Ghidra project in `work/ghidra/` is a local artefact (gitignored), and so is `analysis/native/generated/`, which embeds decompiled proprietary code. Both are regenerated by the commands above.

## 6. File inventory

| path | tracked | content |
|---|---|---|
| `tools/decompilation/smelf.py`, `armdis.py` | yes | shared ELF and disassembly libraries |
| `tools/decompilation/elf_audit.py`, `function_index.py`, `classes.py`, `compare_callgraphs.py`, `ghidra_prepare.py`, `run_ghidra.sh` | yes | pipeline stages |
| `tools/decompilation/ghidra/SmPreAnalysis.java`, `SmExport.java` | yes | Ghidra scripts |
| `analysis/native/elf_audit.<bin>.json` | yes | audit |
| `analysis/native/FUNCTION_INDEX.<bin>.jsonl` | yes | function inventory |
| `analysis/native/function_summary{,.<bin>}.json` | yes | denominators, gaps, call statistics |
| `analysis/native/xrefs.<bin>.json` | yes | resolved data refs and indirect call/jump sites per function (metadata, no code) |
| `analysis/native/classes.<bin>.json` | yes | classes, vtables (slots resolved), typeinfo, OBJECT symbols, `.bss` map |
| `analysis/native/strings_xrefs.<bin>.json` | yes | Ghidra strings and referencing functions |
| `analysis/native/ghidra_export_summary.<bin>.json` | yes | Ghidra counts, failures, list of decompiled addresses |
| `analysis/native/callgraph_crosscheck.<bin>.json` | yes | capstone vs Ghidra edge diff |
| `analysis/native/generated/decomp/<bin>/*.c`, `generated/<bin>_callgraph.json` | **no** (gitignored) | decompiled pseudocode and Ghidra call graph |
| `analysis/evidence/native.jsonl` | yes | evidence ledger `EV-NAT-0001…0039` |
| `docs/BOOT_CHAIN.md` | yes | native boot chain and frame loop |
