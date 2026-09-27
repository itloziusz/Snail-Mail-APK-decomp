# Historical leads — verification against the current APK

Earlier work reportedly produced the leads below. Each was re-checked against
the APK supplied for this pass (SHA-256
`0d10908d50f2a8361d9bbd3c6c9bff025434fdfb49c0f2b97254a793fb0b29e7`). No earlier
project files, Ghidra databases, traces, bridges or renderer code exist in this
workspace or anywhere on the analysis machine (filesystem search on
2026-09-27 for `*plume*`, `*LegacyRenderIR*`, `*snailmail*`, `*.gpr`,
`FUNCTION_INDEX*` returned only the uploaded APK). So every lead about earlier
*implementations* is unverifiable here, and is treated as absent.

| # | Lead | Verdict | Evidence |
|---|---|---|---|
| 1 | Native library `libsnailmail.so` | **Confirmed** | APK entries `lib/armeabi/libsnailmail.so`, `lib/armeabi-v7a/libsnailmail.so`; `System.loadLibrary("snailmail")` in `SnailMailActivity.<clinit>` |
| 2 | armeabi-v7a build and possibly older armeabi build | **Confirmed, both present** | v7a: SHA-256 `e43bc913…a466`, 670,897 B, `Tag_CPU_arch v7`, VFPv2, softfp. v5: SHA-256 `96dbeaeb…8136`, 707,235 B, `Tag_CPU_arch v5TE`, soft-float. They are **different compilations** (different sizes/addresses), not copies. |
| 3 | Symbol-rich / unstripped binary | **Confirmed** | Both have `.symtab`/`.strtab` (v7a: 0xe5c0 bytes of symtab); C++ names are Itanium-mangled (e.g. `_ZN6cRHash3AddEPci`). No DWARF debug sections. |
| 4 | "≈1,183 named functions" | **Confirmed as a raw count only; the interpretation is corrected** | v7a `.symtab`: 1,147 GLOBAL + 25 LOCAL + 11 WEAK defined `FUNC` = **1,183 symbols** at **1,173 unique addresses** (aliases, e.g. C1/C2 constructor variants, share addresses). This includes EABI/libgcc runtime helpers and JNI glue, so it is **not** a count of game functions. v5 has 1,253 defined FUNC symbols at 1,214 addresses. Name-set comparison (established): v5's unique function names = v7a's 1,182 unique names **plus exactly 70 soft-float helpers** (`__aeabi_fadd`, `__adddf3`, `__aeabi_f2iz`, …); v7a has no names absent from v5. So both builds share the same game-level function set by name, differing in float code generation. Categorised counts: see `analysis/native/function_summary.json`. |
| 5 | `assets/asm.mp3` is a custom archive, not MP3 | **Confirmed** | No ID3/MPEG frame sync; consumed by `Java_…_JNIDatInit` (v7a:0x15244) via `AssetManager.openFd("asm.mp3")`. The `.mp3` extension is presumably there to keep aapt from compressing it (aapt stores `.mp3` uncompressed; the entry is `Stored`) — **hypothesis** about intent, but the storage fact is established. |
| 6 | Archive ≈8.19 MB, ≈734 records | **Size confirmed (8,188,993 B); count: first u32 = 734** | Record count semantics are established from consuming code in `docs/ASSET_FORMATS.md`, not from matching this number. |
| 7 | Earlier ELF32 loader, JNI and import-bridging experiments | **Not present — cannot verify** | No code in the repository or on disk. Note these approaches (loading the ARM32 ELF) are **incompatible** with the project's shipping rules anyway; the only ARM32-execution tooling in this pass is the analysis-only Unicorn harness in `tools/validation/arm32_ref/`, used for differential testing and never shipped. |
| 8 | GLES-related experiments | **Not present — cannot verify** | GLES version is established fresh from imports: `libGLESv1_CM.so` NEEDED, 50 fixed-function `gl*` imports, manifest `glEsVersion 0x00010001` (GLES 1.1). |
| 9 | Rendering direction LegacyRenderIR → Plume → Vulkan | **Not present — design proposal only** | No Plume source, headers or binaries are available, so Plume's API has **not** been inspected and nothing in this project depends on it yet. See `docs/ARCHITECTURE.md` for the rendering boundary. |
