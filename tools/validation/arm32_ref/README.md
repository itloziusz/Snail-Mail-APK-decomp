# arm32_ref: run the original ARM32 code as a test oracle

> **Analysis only. Never shipped.** Nothing in this directory is linked into, packaged with, or reachable from the port. The shipping rules forbid ARM32 emulation or translation in the game's execution path. This harness only produces *reference answers* for differential tests (`docs/TESTING.md`).

`armref.py` loads the original `libsnailmail.so` (ELF32 / ARM / ET_DYN) into [Unicorn](https://www.unicorn-engine.org/) and calls individual functions.

## What it does

| Step | Behaviour |
|---|---|
| Load | Maps every `PT_LOAD` at `base` (default `0x40000000`). `.bss` and other `memsz > filesz` tails are zero. Checks the sha256 when `expected_sha256` is given. |
| Relocate | Reads `DT_REL` / `DT_JMPREL` through `PT_DYNAMIC`. It handles `R_ARM_RELATIVE`, `R_ARM_ABS32`, `R_ARM_GLOB_DAT` and `R_ARM_JUMP_SLOT`; any other type is an error. For v7a it applies 936 RELATIVE, 2 ABS32, 2 GLOB_DAT and 94 JUMP_SLOT relocations. |
| Function imports | Each import resolves to its own 8-byte stub (`bx lr`). A `UC_HOOK_CODE` on the stub region dispatches the stub to a Python handler. A stub **without a handler raises `UnknownImportError`**; it never returns a made-up value. |
| Data imports | `__stack_chk_guard` gets real storage. Every other data import (e.g. `__sF`) points into an **unmapped** poison region, so any access raises `GuestFault`. |
| libc model | `malloc`/`free` (bump heap with double-free and unknown-pointer detection), `memset`, `memcpy`/`memmove`, `strlen`, `strcpy`, `strcat`, `dup`, `fdopen`, `fseek`, `ftell`, `fread`, `fclose` (over Python byte strings registered with `add_fd`). `__stack_chk_fail` and `abort` raise. |
| JNI | `make_jnienv({name: handler})` builds a `JNIEnv*` whose 233 function-table slots are separate stubs named `JNI:<Function>`. Only the slots you supply a handler for work. |
| Callbacks | `make_callback(name, fn)` returns a guest function pointer that runs `fn`. It is used to supply `cRHash`'s name getter. |
| Calls | `call(symbol_or_vaddr, *ints, max_insns=…)` follows AAPCS: r0–r3 first, then 8-byte-aligned stack. It returns r0. Exceeding the instruction limit raises `InstructionLimitExceeded`; a guest memory fault raises `GuestFault` with the PC. |
| CPU | ARM mode, USR (set once before any banked-register writes), VFP enabled (CPACR cp10/cp11 plus FPEXC.EN). |

It does **not** run `.init_array` constructors, and it does not model GL, timing, threads or real Java.

## Usage

```python
import sys; sys.path.insert(0, "tools/validation/arm32_ref")
from armref import ArmRef
ref = ArmRef("work/apk_unzip/lib/armeabi-v7a/libsnailmail.so",
             expected_sha256="e43bc913e9ba99abd2fed4d2cee40d4a33a951ecbcf8ca154d099cc8cabaa466")
s = ref.alloc_cstr(b"RANDTABLE.BIN")
print(ref.call("cRHash::Calc(char*)", 0, s))   # -> 20
```

To smoke-test the harness, run `python3 tools/validation/arm32_ref/armref.py`.

## Suitability

The harness is suitable for self-contained routines whose every import has a stub with known semantics: parsing, hashing, table code, and file reads via the file model. Anything that reaches an unmodelled import stops loudly, and that is by design. Floating-point routines need a documented comparison policy before any comparison (`docs/TESTING.md`).

## Users

- `tests/differential/assets/run_diff.py`: `cRHash`, `JNIDatInit`, `RShellDatFind`, `RShellLoadFile`, `PfmLoadFileDat` and `JAVAC_Un*`.
