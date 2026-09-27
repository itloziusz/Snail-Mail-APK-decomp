# Architecture decision: native ARM64 port of Snail Mail

## Revision 2 (2026-09-27): AOT execution path, then incremental source replacement

**Decision:** the playable port runs the original game logic through **static
ahead-of-time translation**. `tools/aot/arm2c.py` converts every function of
the original v7a binary to C at build time, and the C is compiled to AArch64
like any other source. Hand-written source reconstruction (Revision 1 below)
continues and will replace translated functions over time.

**Why the decision changed:** the owner asked for a running game. Only 7 of
1,114 functions were reconstructed. The translation covers all 1,173 functions
now and is checked against the original machine code (below). The project
rules allow AOT when it is "clearly justified and independently validated".

**What runs on the device:**

* **Translated code.** Each original function is a C function over an explicit
  guest CPU state. Integer instructions are decoded from raw encodings so flags
  and carry are exact. VMLA/VMLS/VNMLS keep two roundings (`-ffp-contract=off`),
  and float-to-int conversion saturates as ARM does.
* **Nothing executes at run time except compiled code.** No instruction is
  decoded or translated at run time. Indirect branches and calls select an
  already-compiled function from a static table (`aot_dispatch`). An unknown
  target is a fatal diagnostic, never interpreted. No interpreter, JIT, QEMU or
  ARM32 code is present.
* **Guest memory.** A 4 GiB **virtual reservation** holds the original image,
  heap and stacks at 32-bit guest addresses. Pages are committed only when used:
  a gameplay run has a peak RSS of 27 MiB. Inside the window the original's
  ILP32 layouts and pointer idioms are correct by construction (the in-place
  directory pointers, the pointer in a Java `int`). Host pointers never enter
  guest memory: Java objects are opaque 32-bit handles.
* **Boundaries.** libc/libm/stdio (52 imports), GLES 1.x (45 imports) and JNI
  (14 functions) are hand-written shims in `aot/runtime/`. They are the only
  places where guest values become host values.
* **Remaining unsupported sites.** 44 instructions are untranslatable, all in
  the libgcc exception unwinder's VFP/WMMX save and restore. They are reachable
  only when a C++ exception is thrown; this binary never throws. If reached,
  they are fatal diagnostics.

**Validation.** A reference runner (`tools/validation/arm32_ref/game_ref.c`,
analysis only) executes the **original ARM32 instructions** in Unicorn. It uses
the identical runtime, Java emulation, input script and virtual clock. For a
6,000-frame run through the splash screens, intro, menus and tutorial gameplay:

* The GL call traces are **byte-identical** between the original code in
  Unicorn, the translated build on x86-64, and the translated build compiled as
  AArch64 and run under qemu-aarch64. That is 2,213,927 calls, including hashes
  of every uploaded texture, buffer and matrix.
* The save files are identical.

The reference runs are listed in `docs/PORTING_STATUS.md`.

**Boundary with reconstructed source.** A reconstructed function may replace a
translated one only when:

1. it reads and writes guest state exclusively through the guest-memory
   accessors (or replaces an entire subsystem, including every function that
   touches its data), and
2. the whole-game differential trace stays identical.

Today `sm_assets` / `sm_platform` are validated equivalents and are not linked
into the AOT build.

**Rendering.** On Android the translated code calls the device's GLES 1.1. On
hosts without GLES1 (Ubuntu Mesa), the GLES1-on-GLES2 layer in
`reconstructed/rendering` is used. The Vulkan/Plume direction is unchanged: it
would sit behind the same 45-entry `sm_gl_backend` interface.

---

## Revision 1 (superseded for the execution path, still the long-term direction)

Status: **accepted for the next passes**; revisit if the evidence below changes.
Reference binary: `v7a` (`lib/armeabi-v7a/libsnailmail.so`, SHA-256
`e43bc913e9ba99abd2fed4d2cee40d4a33a951ecbcf8ca154d099cc8cabaa466`).

## 1. Decision

**Port by native source reconstruction.** Recover the original algorithms as
maintainable C/C++ modules and compile them for `arm64-v8a`. Static AOT
translation is **not** used, and no AOT code exists. The shipping APK contains
no ARM32 code, interpreter, JIT, binary translator or emulator.

## 2. Evidence for the decision

| Evidence | Source | Why it favours source reconstruction |
|---|---|---|
| Unstripped `.symtab` with Itanium-mangled C++ names; 1,173 unique function starts, of which 1,096 are game/engine, 18 JNI, 59 runtime helpers | `analysis/native/function_summary.v7a.json` | Class, method and parameter types are known for most functions, which is a strong scaffold for source |
| `.text` 447,124 B; function symbols cover all but 8 bytes; 1,151 of 1,173 functions agree with `.ARM.exidx` bounds | same | Function boundaries are essentially solved; no mixed-mode or data-in-code guesswork beyond literal pools |
| Pure ARM mode (no `$t` mapping symbols), GCC 4.4.0, `-O2`-style code | `analysis/native/elf_audit.v7a.json` | Compiled C++ of a small engine, not hand-written assembly |
| Small import surface: libc stdio/string/malloc, a few libm calls, 45 GLES 1.x entry points, JNI; **no** native middleware (no zlib, libpng, libogg, OpenAL) | dynsym imports | Decoding of zip/PNG/JPEG and all audio is delegated to Java callbacks, so there are no third-party native libraries to port |
| No `JNI_OnLoad`, no `RegisterNatives`, no `dlopen`/`dlsym` | dynsym, `docs/JNI_MAP.json` | The whole Android↔native contract is the 18 name-bound JNI exports plus the Java callbacks, and it can be reproduced exactly |
| 78 indirect call sites, 46 switch tables | function summary | Indirect control flow is bounded and tractable by hand; an AOT recompiler's main advantage is not needed |
| First reconstructed module reference-validated against the original instructions with 0 mismatches | `tests/differential/assets/result.json` | The reconstruct-then-differentially-test loop works on this binary |

**Why not AOT translation.** AOT would keep the original ILP32 memory model: a
simulated 32-bit address space, in-place 32-bit pointer fix-ups in loaded data
(JNIDatInit writes `gDat + name_offset` into records), and a Java `int` carrying
a native pointer. That is exactly the class of hazard this port should remove.
Source reconstruction removes these hazards through explicit conversions (see
`sm_assets`, which stores offsets instead of patched pointers).

## 3. Module structure and boundaries

```
Android (Java shell, same class/method names as the original)
   │  JNI: 18 name-bound exports  +  Java callbacks (JAVA* methods)
   ▼
reconstructed/platform   JNI bridge, file/asset I/O, time, input queue,
   │                     audio/music requests → Java, logging
   ▼
reconstructed/core       game logic (cR* engine classes, t* math types)
   │           │
   │           └──▶ reconstructed/assets   archive directory, name hash,
   │                                        record decode spans, TGA placement
   ▼
reconstructed/rendering  G0* render layer → render boundary (see §4)
reconstructed/audio      cRSound / voice manager → platform audio requests
```

Rules:

* Game logic in `core/` never calls JNI, GL or libc I/O directly. It calls the
  platform and rendering interfaces, which are recovered from the original call
  sites (`analysis/native/platform_imports.json`, `docs/PLATFORM_BOUNDARIES.md`).
* Serialized data (the archive, saves, `.SMO`/TXT/`.X` assets) is decoded by
  explicit little-endian readers into native objects. No packed on-disk struct
  is overlaid on memory, and no 32-bit pointer is stored in data.
* The JNI contract keeps the original Java class names and JNI descriptors, so
  the exported symbol names stay identical (`Java_com_sandlotgames_snailmail_*`).
  The application ID differs (`com.sandlotgames.snailmail.port`) so the port
  never replaces the original app.

## 4. Rendering

The original renders through a small GLES 1.1 fixed-function subset, recovered
at 283 call sites (`analysis/native/gl_usage.json`):

* **Buffers and draws:** VBOs with `GL_STATIC_DRAW`; `glDrawElements` with
  `GL_TRIANGLES`, `GL_TRIANGLE_STRIP` and `GL_LINES`, `GL_UNSIGNED_SHORT`
  indices.
* **Vertex formats:** position `3×GL_FLOAT` with stride 20 or `3×GL_SHORT` with
  stride 10; texcoords `2×FLOAT`/`2×SHORT`.
* **Fixed-function state:** texenv `GL_MODULATE`; fog (density constant
  0x3ca3d70a ≈ 0.02); flat shading; the texture matrix is used.
* **Blending and depth:** 5 blend-function pairs; depth `GL_LEQUAL`; toon
  outlines use a `glDepthRangef(-0.004, 0.996)` offset.
* **Not observed:** no lighting calls and no normal or colour arrays enabled.

The plan has two stages:

1. **Reference backend: GLES 1.1 on the device.** `libGLESv1_CM.so` is an
   arm64 system library on arm64 Android, and calling it is not CPU emulation.
   The Android CDD requires GLES 1.x support; that should be re-checked against
   the current CDD text before relying on it (**likely**, not verified here).
   This backend gives visual equivalence with the least new code, and it is the
   baseline for screenshot comparisons.
2. **Vulkan backend (planned).** The G0* layer will target a recorded command
   interface ("LegacyRenderIR") that captures exactly the state listed above.
   A Vulkan backend (via Plume if its source is brought in and its API
   inspected) can then consume that IR. The IR is a graphics abstraction, never
   a CPU-execution mechanism. Plume is **not** available in this workspace and
   has not been inspected.

Visual equivalence comes before any enhancement: no gamma, fog, lighting,
texture or camera changes.

## 5. Numeric semantics

* The original is ARMv7 VFPv2 with softfp argument passing (v7a) or a
  soft-float library (v5). The two builds can differ numerically. v7a is the
  reference.
* Reconstructed code builds with `-ffp-contract=off`, so AArch64 does not fuse
  multiply-adds that the original computed with two roundings.
* Fixed-width integers (`int32_t`/`uint32_t`) are used where the original's
  32-bit wrap or sign matters. Plain `char` is unsigned on both ARM EABI and
  AArch64, but reconstructed code still spells out signedness.
* libm, `lrand48` and `qsort` differences are tracked in `docs/ABI_PORTING.md`.

## 6. Unresolved risks

* **Float semantics** (FPSCR flush-to-zero and default-NaN on the original
  device, libm precision) are uncharacterised, and no float routine is
  reference-validated yet.
* **Bitmap decoding:** `BitmapFactory` output (premultiplied alpha, JPEG
  decoder differences) feeds textures, and needs a device capture.
* **Timing:** the frame and time model (`JAVATime` vs `gettimeofday`) must be
  recovered before any simulation code is trusted (`docs/BOOT_CHAIN.md`).
* **Unanalysed formats:** 364 `.SMO` records and the TXT, `.X` and table
  formats are not yet analysed.
* **Toolchain:** no NDK is available in this environment. The development APK
  is built with an NDK-less clang/lld pipeline (`tools/android_build/`), which
  must be replaced by the pinned NDK build.
