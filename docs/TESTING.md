# Testing policy and current test inventory

## Levels of evidence

| Level | What it shows | What it does NOT show |
|---|---|---|
| Structural validation | Parser accepts the real data, all offsets are in bounds, counts are self-consistent | That the original game interprets the data the same way |
| Unit test (`UNIT_TESTED`) | Reconstructed code agrees with expectations written from the recovered spec | That the spec is right |
| Differential vs original code (`REFERENCE_VALIDATED`) | Reconstructed code and the **original ARM32 instructions** give identical outputs and side effects for the tested inputs | Behaviour for untested inputs, or when the routine runs inside the full game (JNI, GL, timing) |
| Original-execution capture | Behaviour of the real app on a real device | — (not available in this pass: no device) |
| `ARM64_DEVICE_VALIDATED` | Reconstructed code runs as AArch64 on a real arm64 Android device | — (NOT RUN in this pass) |

A passing test shows agreement for its tested conditions only.

## Reference runtime used in this pass

There is no Android device or 32-bit Android runtime here. The reference for
isolated, platform-free routines is the **original ARM32 machine code** run in
the Unicorn CPU emulator by `tools/validation/arm32_ref/armref.py`:

* It loads the original `libsnailmail.so` segments, applies its dynamic
  relocations, and resolves imports to Python stubs. An import without a stub
  raises an error; it never returns a fake result.
* It is **analysis-only**. It is never linked into, packaged with, or reachable
  from the port. The shipping rules forbid ARM32 emulation in the game's
  execution path; using an emulator as a test oracle is outside that rule.
* It is suitable only for routines whose dependencies are fully stubbed with
  known semantics (e.g. hashing, parsing, math). Routines that touch GL, JNI or
  timing need an original-execution capture instead.

## Comparison policy

* Discrete values, byte formats, hash values, indices: **exact** comparison.
* Floating point: no tolerance is defined yet. Before any float routine is
  compared, its reference behaviour (VFPv2 softfp in v7a; soft-float library in
  v5) must be characterised and a justified policy recorded here. Tolerances are
  never loosened to make a failing implementation pass.
* Every differential result records: reference binary SHA-256, inputs,
  comparison method, mismatch count and first divergence.

## AArch64 execution of tests

Unit tests are cross-compiled with `cmake/aarch64-linux-gnu.toolchain.cmake`
and run as AArch64 machine code under `qemu-aarch64` (user-mode). This checks
64-bit type and alignment behaviour of the reconstructed code on the target ISA.
It is **not** Android and not device validation: it uses glibc, not bionic.

## Commands

```sh
# host, with ASan+UBSan
cmake -S . -B build-host -DSM_SANITIZE=ON && cmake --build build-host -j
ctest --test-dir build-host --output-on-failure

# AArch64 (runs under qemu-aarch64 via CMAKE_CROSSCOMPILING_EMULATOR)
cmake -S . -B build-a64 -DCMAKE_TOOLCHAIN_FILE=cmake/aarch64-linux-gnu.toolchain.cmake
cmake --build build-a64 -j && ctest --test-dir build-a64 --output-on-failure
```

Tests that need the original archive look for `work/apk_unzip/assets/asm.mp3`
(created by `tools/inventory/setup_workspace.sh`) and report SKIP when it is
absent.

## Current inventory

See the "Tests performed" section of `docs/PORTING_STATUS.md` for the
commands actually run in each pass and their results.
