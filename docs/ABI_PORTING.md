# ARM32 → AArch64 ABI and semantics audit

The port reconstructs source code; no ARM32 code runs in the shipped app
(no interpreter, binary translation, JIT, QEMU or hidden 32-bit library).
This document lists every ABI or machine-semantics difference that can make
reconstructed AArch64 code behave differently from the original ARM32
machine code, with evidence from **this** binary, the risk, and the rule that
reconstructed code must follow. Binaries, address format, confidence words and
evidence IDs as in `docs/CONVENTIONS.md` / `analysis/evidence/platform.jsonl`
(`v7a` = `e43bc913…a466`, `v5` = `96dbeaeb…8136`; addresses are v7a unless
prefixed).

Reproduce the numbers:

```sh
python3 tools/validation/abi/abi_audit.py                 # instruction/ABI census (both builds)
python3 tools/validation/abi/unicorn_runtime_probe.py     # runs original leaf routines under Unicorn
python3 tools/validation/abi/trig_table_sensitivity.py    # libm sensitivity of the sin/cos tables
tools/validation/abi/c/run_abi_c_checks.sh                # rand48 / float->int / FP-contraction on host, AArch64 (qemu), ARMv7 (qemu)
```

## Summary

| § | Topic | Evidence (short) | Risk | Rule |
|---|---|---|---|---|
| 1 | pointer truncation / pointers in 32-bit slots | archive directory fix-up 0x153e0; Java `int` pointer 0x7d42c; raw struct saves | **high** | offsets not pointers; handle tables; explicit serialisers |
| 2 | `long`/`size_t`/`time_t` 32→64 | `_Znwj`, `mla` time math 0x154f4 | medium | fixed-width types, explicit wrap |
| 3 | struct layout / alignment | `Tag_ABI_align_needed 8`, 0x130/0x2d00-byte memory-image saves, 24-byte records | **high** | `static_assert` sizes/offsets of every persisted struct |
| 4 | enum size | `Tag_ABI_enum_size: int` | low | fixed-width types in persisted structs |
| 5 | `wchar_t` | `Tag_ABI_PCS_wchar_t: 4`; no wide-char API used | low | — |
| 6 | plain `char` signedness | 0 `LDRSB`/0 `SXTB` in game code; `uxtb` before `ConvertCode(char)` 0x13ddc | medium (host tests) | `-funsigned-char` everywhere + explicit types |
| 7 | FP calling convention | softfp (no `Tag_ABI_VFP_args`), v5 soft-float | low (source port) / relevant for harness | — |
| 8 | FP contraction | 711 `VMLA`, 38 `VMLS`, 66 `VNMLS`, 0 fused; Unicorn shows two roundings | **high** | `-ffp-contract=off`, no `fma()` |
| 9 | float→int conversion | 172 `vcvt.s32.f32`, 8 `vcvt.u32.f32`; `tColourSmall::Set` wraps | **high** | ARM-semantics conversion helpers |
| 10 | FPSCR/FTZ/default-NaN | no `vmsr`/`vmrs`→GPR; no NEON | low (likely) | never enable FTZ/DAZ or fast-math |
| 11 | v5 soft-float vs v7a VFP | helpers IEEE; probes identical | low/medium | treat v7a as reference |
| 12 | libm differences | 10 libm calls; trig tables insensitive (0/65536) | medium | tolerance tests; vendor libm if needed |
| 13 | integer division | libgcc helpers, `/0` → 0, `%0` → dividend | medium | explicit division helpers |
| 14 | overflow / shifts | wrapping code; 2 variable-shift sites | medium | `-fwrapv`, shift helper |
| 15 | `lrand48`/`srand48` | persisted `RandTable.bin`; glibc default seed ≠ BSD | **high** (determinism) | own rand48 implementation |
| 16 | `qsort` | 2 calls, 4×`int`, `*a-*b` comparator 0x23480 | low | wrap-exact comparator |
| 17 | variadics | `sprintf`×50, `vsprintf`×3, custom `wprintf`, JNI `Call*Method` | medium | `va_*` only, `-Wformat`, rename `wprintf` |
| 18 | function-pointer tables / static init order | 42 vtables, 11 `.init_array`, 3 GOT code pointers | medium | no cross-TU init-order dependence |
| 19 | ARM/Thumb interworking | none | none | — |
| 20 | exceptions / unwind / RTTI | EHABI tables; no throw; no RTTI | low | `-fno-exceptions -fno-rtti` acceptable |
| 21 | TLS | none | none | — |
| 22 | atomics / concurrency | no LDREX/STREX/SWP/DMB; racy cross-thread globals | medium | decide & document synchronisation |
| 23 | inline asm / SIMD / coprocessor / SVC | none in game code | none | — |
| 24 | loader: TEXTREL, 4 KB pages | `DT_TEXTREL`, `p_align 0x1000` | n/a for port | checker rules E2–E4 |

---

## 1. Pointer truncation and pointers stored in 32-bit slots (EV-PLAT-0018, 0041, 0049)

* **Archive directory fix-up.** `JNIDatInit` reads the `asm.mp3` directory
  into a malloc'd block and, for each 24-byte record `i`
  (`r12 = base + i*24`, 0x153e0/0x153f0), executes `ldr r1,[r12,#4]; add
  r1,r3,r1; str r1,[r12,#4]` (0x153f4–0x153fc): the 32-bit *offset* at
  record+4 becomes an absolute *pointer* in place. Consumers read it back as a
  pointer: `DatHashGetString(int)` 0x19954 returns `[base + i*24 + 4]`,
  `RShellDatFind` 0x1b920 returns `&record+4`. On AArch64 a pointer does not
  fit into those 4 bytes. **Rule:** keep the on-disk 24-byte record layout as
  data (`static_assert(sizeof == 24)`), keep the field as `uint32_t` offset and
  form `base + offset` at the point of use (the assets area already owns the
  format).
* **Java `int` carrying a native pointer.** `JAVAOpenFeintSubmit/Unlock`
  pass a native address as `int CBOFOStatePtr`; Java hands it back to
  `JNIOFOSubmitCB/JNIOFOUnlockCB`, which store through it
  (`cmp r5,#0; movne r3,#2; strbne r3,[r5]` at 0x7d420–0x7d42c and
  0x7d468–0x7d474). **Rule:** never cast pointers to `jint`; use a small
  integer handle table (or keep the path unreachable while OpenFeint stays
  removed — the port's Java shell only ever reports `0`).
* **Pointer-containing tables.** `gSFXBank` (0x8bacc, 12-byte records whose
  first word is a relocated `char*`), `gJAVAFunction` (0x8b3f0, 12 bytes:
  `jmethodID`, `char*`, `char*`), 42 vtables. Their sizes change on AArch64;
  harmless in reconstructed source **unless** code indexes them with literal
  strides/offsets. **Rule:** symbolic field access only.
* **Very large objects accessed by constant offsets.** `new cRGame` with
  `operator new(0x3a6468)` (0x16684–0x1668c); fields reached as `Game+0x718b4`,
  `+0x718fc`, `+0xbf0`, `+3028`, `+552`. Any pointer inside `cRGame` shifts
  those offsets on AArch64. **Rule:** reconstruct named members; tests that
  compare memory with the original must map fields, not raw offsets.
* **Pointers in saved data** — see §3: `asm.cfg`/`of.cfg` are raw memory
  images; whether they contain pointer-sized fields is open (PLATFORM Q2).

## 2. `long`, `size_t`, `ptrdiff_t`, `time_t`: 32 → 64 bits (EV-PLAT-0045, 0055)

ILP32 → LP64. Direct evidence: the only `operator new` import is `_Znwj`
(`size_t` = `unsigned int`), `ftell`/`fseek` use 32-bit `long`, `lrand48`
returns `long`. `_getTime()` 0x154c0 computes `tv_sec*1000 + tv_usec/1000`
with a 32-bit `mla` (0x154f4) — the result wraps modulo 2³², whereas with
64-bit `time_t`/`long` the same C expression would not. **Rule:** every value
that was 32-bit in the original is `int32_t`/`uint32_t` in reconstructed code;
where wrap-around is part of the behaviour, compute in `uint32_t`
explicitly. Never use `long`, `size_t` or `time_t` for game state or file
fields.

## 3. Struct packing and alignment (EV-PLAT-0007, 0029)

Attributes (both builds): `Tag_ABI_align_needed: 8-byte`,
`Tag_ABI_align_preserved: 8-byte, except leaf SP`. ARM EABI aligns
`double`/`long long` to 8, as AArch64 does (unlike i386), so non-pointer
structs keep their layout; pointer (and `long`) members change it. Persisted
or externally defined layouts found so far: archive header 244 bytes
(`fread(…,1,0xf4,…)` 0x15368), 24-byte directory records, `gConfig` 0x130
bytes and `gOFOData` 0x2d00 bytes written as raw memory (`asm.cfg`, `of.cfg`),
`gRMathRand2Table` 0x3ffe bytes. **Rule:** each persisted struct gets fixed-
width members, explicit little-endian (de)serialisation or
`static_assert(sizeof/offsetof)` against the original values; no pointer or
`long` members inside.

## 4. Enum size — `Tag_ABI_enum_size: int`

Enums are 4 bytes, as on AArch64 for enums fitting in `int`. Risk only if an
enum sits in a persisted struct and the reconstruction uses a different
underlying type. **Rule:** persisted fields use `int32_t`, not enum types.

## 5. `wchar_t` — `Tag_ABI_PCS_wchar_t: 4`

4 bytes on both targets (GCC: `unsigned int` for arm-linux-gnueabi and
aarch64-linux-gnu). No wide-character function is imported. Note the name
collision in §17.

## 6. Plain `char` is unsigned (EV-PLAT-0038)

ARM EABI makes plain `char` unsigned; AArch64 Linux/Android too; x86-64 hosts
(where the unit tests run) make it signed. Evidence that the game relies on
the unsigned behaviour or at least never used signed chars: game code has
**0 `LDRSB` and 0 `SXTB`** against 939 `LDRB`/79 `UXTB` (v7a; v5: 0/0 vs
948), and callers zero-extend `char` arguments: `JNIKey` does `uxtb r1,r2`
before `bl cKeyPad::ConvertCode(char)` (0x13ddc/0x13de8). **Rule:** compile
every build (host, sanitizer, AArch64, NDK) with `-funsigned-char`; write
`unsigned char`/`int8_t` explicitly where the sign matters.

## 7. FP argument passing: softfp (v7a) / soft-float (v5) vs AArch64

v7a has no `Tag_ABI_VFP_args` ⇒ base AAPCS: `float` arguments/results travel
in `r0–r3`/stack as bit patterns (e.g. `vmov s13, r0` at 0x1408c); v5 has no
FPU at all and calls libgcc (`__mulsf3` 2066 calls, `__aeabi_fadd` 1659, …).
AArch64 passes FP values in `v0–v7`. Irrelevant for reconstructed C/C++;
relevant for (a) the Unicorn reference harness (arguments go into core
registers — see `unicorn_runtime_probe.py`), and (b) JNI varargs, where
`float` is promoted to `double` (`vcvt.f64.f32` before `CallIntMethod`,
0x1423c; EV-PLAT-0046).

## 8. FP contraction: VFPv2 VMLA is not fused (EV-PLAT-0034, 0035)

Census (v7a game code): 24 193 VFP instructions; `vmla` 711, `vmls` 38,
`vnmls` 66, `vnmul` 13, `vdiv` 362, `vsqrt` 3; **no** `vfma/vfms`
(VFPv4). The ARMv7 ARM defines VFP `VMLA/VMLS/VNMLA/VNMLS` as multiply, round,
then add/subtract, round (two roundings). Unicorn execution of the original
`tVector::Dot` (0x16aa4: `vmul` then two chained `vmla`) returns `0.0` for
inputs where a fused evaluation gives ±2⁻⁴⁶ (probe [1]). The same C code
compiled for AArch64 with GCC's default (`-std=gnu*` ⇒ `-ffp-contract=fast`)
or clang's default emits two `FMADD`s and returns `+1.42e-14` (GCC) /
`−1.42e-14` (clang); with `-ffp-contract=off` it returns `0.0`
(`run_abi_c_checks.sh`). **Rules:** `-ffp-contract=off` in every build (the
top-level `CMakeLists.txt` and `android/app/src/main/cpp/CMakeLists.txt` set
it); no `fma()`/`std::fma`; keep the operand grouping visible in the `vmla`
chains (e.g. `Dot = (a.y*b.y + a.x*b.x) + a.z*b.z` — addition commutes
exactly, association does not); all arithmetic stays `float` where the
original used `.f32` (FLT_EVAL_METHOD 0 on both targets; `vcvt.f64.f32` appears
only around libm calls: 29 sites).

## 9. Float → integer conversion (EV-PLAT-0036)

`vcvt.s32.f32` (172 sites) / `vcvt.u32.f32` (8) round toward zero and
**saturate**; NaN → 0. v5 reaches the same results through libgcc
`__aeabi_f2iz`/`__aeabi_f2uiz` (Unicorn run of the original v5 routines:
`2147483648.0 → 0x7fffffff`, `-1.5 → 0` unsigned, `NaN → 0`, …). In C an
out-of-range conversion is undefined: AArch64 `FCVTZS/FCVTZU` happen to
saturate like ARM, x86-64 `CVTTSS2SI` returns `0x80000000` (7 of 12 probe
inputs differ on the x86 host, 0 on AArch64/ARMv7 — `f2i_check`). The game
depends on it: `tColourSmall::Set(float r,g,b,a)` 0x1811c stores
`(uint8_t)(uint32_t)(x*255.0f)` per channel, so `1.2 → 50` (wraps), `2.0 → 254`,
`-0.1 → 0` (saturates) — identical in v7a and v5 under Unicorn, different on
x86 with a plain cast (`-0.1 → 231`). **Rule:** every float→int conversion
whose input is not provably in range goes through helpers with ARM semantics
(proposed `sm_f2i32_arm` / `sm_f2u32_arm`, reference implementation in
`tools/validation/abi/c/f2i_check.c`).

## 10. FPSCR: rounding, flush-to-zero, default NaN (EV-PLAT-0034)

The binary never writes FPSCR (0 `vmsr`) and never reads it into a core
register; its 865 `vmrs` are all `APSR_nzcv, fpscr` flag transfers after
compares. So the app runs with whatever the OS set: on ARM Linux the initial
FPSCR is 0 (round-to-nearest, FZ=0, DN=0) — *likely*, from kernel behaviour,
not observable in the binary. `Tag_ABI_FP_denormal: Needed` and
`Tag_ABI_FP_number_model: IEEE 754` agree. No NEON is used (NEON would
flush denormals on ARMv7). AArch64 Android starts with FPCR = 0 as well
(*likely*). **Rule:** never enable FTZ/DAZ, never build game code with
`-ffast-math`/`-Ofast`/`-funsafe-math-optimizations`; include denormal and
NaN inputs in unit tests.

## 11. v5 (soft-float) vs v7a (VFP) builds may differ numerically (EV-PLAT-0036)

They are different compilations. libgcc soft-float add/sub/mul/div are
correctly rounded like VFP; v7a's chained `vmla` equals v5's separate
`__mulsf3`+`__aeabi_fadd`; conversions matched in the probes; v5 calls libm
`sqrt` (3 sites) where v7a uses `vsqrt.f64` (both correctly rounded).
Remaining risk: compiler-level differences (constant folding, evaluation
order) between the two builds — *hypothesis* that results agree, to be
checked function by function in differential tests. **Rule:** v7a is the
reference; a v5 disagreement is recorded, not averaged.

## 12. libm: bionic 2011 vs today (EV-PLAT-0047)

| Call (v7a site) | Precision | Caller |
|---|---|---|
| `sin`, `cos` (0x196fc, 0x196f0) | double | `RMathInit`: 2×32768-entry `float` tables `RMathSin`/`RMathCos` |
| `acos` 0x18fc4, `atan` 0x18f1c, `exp` 0x18fa0, `pow` 0x18f7c, `sqrt` 0x1832c | double (from float wrappers `ACos`, `ATan`, `Exp`, `Pow`, `Sqrt`) | math helpers |
| `floor` 0x1f814 | double | `cRObject::RequestAnim` |
| `floorf` 0x77d6c, `tanf` 0x7b0f8 | float | `cGLVertexArray::WorldFlatten`, `gluPerspective` |

`floor`/`floorf`/`sqrt` are exact. The trig tables are computed exactly as
the original does (`RMathInit` 0x196d4–0x19728: `a = f32(f32(i·2⁻¹⁵)·2)·π_f`,
`float(sin((double)a))`); `trig_table_sensitivity.py` finds **0 of 32 768**
entries per table whose `float` value changes under a ±2-ulp perturbation of
the double result, so libm choice cannot change those tables (host libm as
reference). `pow`, `exp`, `acos`, `atan`, `tanf` may differ by an ulp between
libms. **Rule:** unit tests compare these with a tolerance unless a
difference is shown to matter; if it does, vendor the 2011 bionic (FreeBSD
msun) implementation for that function.

## 13. Integer division (EV-PLAT-0037)

ARMv7-A without the IDIV extension: all divisions go through the statically
linked libgcc helpers (`__aeabi_idivmod` 39 and `__aeabi_idiv` 13 calls from
game code; plus `__aeabi_uldivmod` in `JAVATime`). Unicorn on the original:
`7/0 → 0`, `-7/0 → 0`, `7%0 → 7` (remainder = dividend),
`INT_MIN/-1 → INT_MIN`, truncating division (`-7/2 → -3 r -1`) — `__div0`
(0x7ef24) is a bare `bx lr`, so no trap. AArch64 `SDIV/UDIV` give the same
results in hardware, but in C both cases are undefined behaviour and x86-64
traps. **Rule:** where a divisor can be zero (or `INT_MIN/-1` can occur),
use explicit helpers with the ARM results; never rely on UB.

## 14. Integer overflow, wrap-around and shifts (EV-PLAT-0044)

The machine code wraps on overflow (e.g. `_getTime` §2, the `*a-*b`
comparator §16). **Rule:** `-fwrapv` (set in the Android CMake; proposed for
the top-level build) *and* explicit `uint32_t` arithmetic where wrap is
meaningful. Register-controlled shifts: ARM uses the bottom byte of the shift
register (LSL/LSR by 32–255 → 0; ASR → sign fill), AArch64 `LSLV` uses the
amount modulo 32/64, C makes ≥ width undefined. Game-code sites:
`RShellBlankTga` 0x1a3b0 (`lsl r6,r3,r11`), `cRSpriteManager::New` 0x2deac
(`orr r5,r2,r1,lsl r5` = `flags |= 1 << n`), `cRSubGame::AI` 0x730f4 (amount
provably 6), plus dead `gRegister*` code. **Rule:** a helper with ARM shift
semantics unless the amount is proven < 32.

## 15. `lrand48`/`srand48` (EV-PLAT-0031, 0032, 0048)

POSIX specifies the 48-bit LCG `X ← (0x5DEECE66D·X + 0xB) mod 2⁴⁸`,
`lrand48 = X >> 17`, `srand48(s): X = (uint32)s << 16 | 0x330E`. Verified:
glibc `lrand48` after `srand48` equals an explicit implementation for 7 seeds
× 100 000 draws on x86-64, AArch64 (qemu) and ARMv7 (qemu). **But** the
*unseeded* state is implementation-defined: glibc starts from `X=0` (first
value 0), the BSD code that bionic inherited uses `0x1234ABCD330E` (first
value 851401618) — bionic-2011 equivalence is *likely*, not verified (Q1). The
game depends on the unseeded state: `gRMathRand2Init` fills and persists
`RandTable.bin` from unseeded `lrand48` on first run (docs/PLATFORM_BOUNDARIES.md §8).
`RandSeed` stores `seed % 8191` (magic `0x80040021` multiply, 0x19588) as a
u16 and `Rand()` indexes with an unsigned `mod 8191` (0x167f0). **Rules:**
never call the platform `lrand48`/`srand48`; implement rand48 explicitly with
the default state as a named constant (hypothesis until Q1); implement the
index arithmetic with the original signedness; tests pin `RandTable.bin`.

## 16. `qsort` (EV-PLAT-0043)

Two calls, both in `ObjectProcSimplifyFaces` (0x2c588, 0x2c59c):
`qsort(p, 4, 4, ObjectProcVertexCompare)`; the comparator (0x23480) is
`return *(int*)a - *(int*)b` (`rsb r0,r0,r3`). Elements are plain `int`s, so
equal elements are indistinguishable and `qsort`'s instability cannot change
the result; the only hazard is the subtraction overflowing for differences
≥ 2³¹ (inconsistent ordering). **Rule:** keep a comparator with the same
wrap-around result (`(int32_t)((uint32_t)a - (uint32_t)b)`) or prove the
inputs small; any sort of 4 ints with a consistent comparator is unique.

## 17. Variadic functions (EV-PLAT-0033, 0046)

`sprintf` ×50 (formats include `%f`, `%02i%%`, `%08i`), `vsprintf` ×3 into a
4096-byte stack buffer (`RShellPrintText/Warning/Error`, 0x1b2b8…, overflow
possible), custom variadics `wprintf(char*, …)` (0x19948, empty) and the
`RShell*` printers, JNI `Call{Int,Void}Method` wrappers (0x140e4, 0x13e84)
forwarding a `va_list` to `Call*MethodV`. ARM's `va_list` is a single pointer;
AArch64's is a 32-byte struct with separate GP/FP save areas, so any code
that copies or walks a `va_list` by hand breaks. **Rules:** only
`va_start/va_copy/va_arg/va_end`; mark printf-like functions with
`__attribute__((format(printf, …)))` (`-Wformat=2` is already on); use
`vsnprintf` with the original buffer size (truncation replaces UB overflow);
rename the native `wprintf(char*, …)` (e.g. `sm_wprintf`) to avoid clashing
with the C library's `int wprintf(const wchar_t*, …)`.

## 18. Function-pointer tables and static initialisation (EV-PLAT-0042)

Code pointers exist only in: 42 vtables in `.data.rel.ro` (one virtual
function each, `AI()` or `cRGame::LevelInit`, typeinfo slot 0 — no RTTI),
11 `.init_array` constructors in link order `Ad.cpp, RMaths.cpp, RShell.cpp,
RObject.cpp, Font.cpp, RSprite.cpp, Game.cpp, SubGame.cpp, Voice.cpp,
LoadingBar.cpp, GL.cpp` (0x8ab5c–0x8ab84), and 3 GOT entries
(`ObjectProcVertexCompare` for `qsort`, `DatHashGetString` and
`TexturesFunctionGetName` for `cRHash::Init(int, char*(*)(int))`). No
PC-relative function-address formation exists. **Rule:** reconstructed static
constructors must not depend on cross-translation-unit order (or the order
above must be reproduced explicitly, e.g. one init function).

## 19. ARM/Thumb interworking (EV-PLAT-0006)

None: 0 `$t` mapping symbols, no odd function addresses, no `BLX <imm>`;
`BLX <reg>` occurs only in the libgcc unwinder (0x7f18c, 0x7f778, 0x7f890).
Pure ARM state in both builds.

## 20. Exceptions, unwinding, RTTI (EV-PLAT-0040)

`.ARM.exidx` has 1151 entries (v7a: 962 compact inline, 173 `.ARM.extab`
references, 16 `EXIDX_CANTUNWIND`); the EHABI unwinder and personality
routines come from libgcc (statically linked; weak imports
`__cxa_begin_cleanup`, `__cxa_type_match`, `__cxa_call_unexpected`,
`__gnu_Unwind_Find_exidx`). No `__cxa_throw`, `__cxa_allocate_exception` or
`__cxa_begin_catch` is imported or defined and there are no `_ZTI`/`_ZTS`
symbols: the library neither throws nor catches C++ exceptions and was built
without RTTI. **Rule:** reconstructed code may use `-fno-exceptions
-fno-rtti`; failure paths must not rely on exceptions.

## 21. TLS (EV-PLAT-0039)

None: no `PT_TLS`, no `.tdata/.tbss`, no TLS relocations, no CP15 c13 reads.

## 22. Atomics and concurrency (EV-PLAT-0039)

No `LDREX/STREX/SWP/DMB/DSB/ISB/CLREX` and no CP15 barrier writes. The only
synchronisation is the thread-safe static guard in `tVector::Cross`
(`__cxa_guard_acquire/release`). Input natives (UI/sensor threads) write
globals read by the GL thread without barriers — a data race in the original,
on a weakly ordered CPU just like AArch64. **Rule:** the port either queues
input to the GL thread or uses `std::atomic` for the handful of shared
fields; the choice is recorded as a behaviour decision.

## 23. Inline assembly, SIMD, coprocessor, system calls (EV-PLAT-0034, 0039)

No NEON; no VFPv3-only encodings (no `vmov` FP immediates, no fixed-point
`vcvt`), D16–D31 appear only in libgcc save/restore helpers; `LDC2/STC2`
appear only in libgcc's iWMMXt save/restore (`__gnu_Unwind_{Save,Restore}_WMMXC`,
unreachable on Android CPUs); no `SVC`, `BKPT` or `UDF`. Nothing needs
hand-written assembly.

## 24. Loader-level facts (EV-PLAT-0004, 0051)

Both builds: `ET_DYN`, `PT_LOAD p_align 0x1000`, `DT_TEXTREL`,
`PT_GNU_STACK RW`, `.bss` 0x31fbdc bytes. The port must be ELF64 AArch64 with
`p_align ≥ 0x4000`, no text relocations and a non-executable stack; this is
checked by `tools/validation/platform/check_elf_alignment.py` (see
`docs/BUILDING.md`).

## Compiler flags required by this document

`-ffp-contract=off -funsigned-char -fwrapv -fno-strict-aliasing`, never
`-ffast-math`/`-Ofast`; linker `-z max-page-size=16384`. The Android native
build sets all of them; the top-level `CMakeLists.txt` currently sets
`-ffp-contract=off -fno-strict-aliasing` but **not** `-funsigned-char` or
`-fwrapv` (proposal to the lead).
