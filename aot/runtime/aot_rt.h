/*
 * Runtime contract for the ahead-of-time translated game code
 * (tools/aot/arm2c.py -> aot/generated/aot_funcs_NN.c).
 *
 * Execution model (docs/ARCHITECTURE.md "AOT execution path"):
 *  - Every original function is a C function `void F_xxxxxxxx(aot_cpu *c)`
 *    compiled natively (AArch64 on devices). No instruction is decoded,
 *    interpreted or translated at run time.
 *  - Guest state: 16 core registers, NZCV, 32 VFP single registers (d0-d15
 *    alias them), FPSCR NZCV. Inside a function the registers are C locals;
 *    they are written back to `aot_cpu` around calls (AOT_SAVE/AOT_LOAD).
 *  - Guest memory: a 4 GiB virtual reservation at `aot_mem`; guest address g
 *    lives at aot_mem + g. Only pages that are really used are committed. The
 *    original's 32-bit pointers therefore stay 32-bit inside the guest; host
 *    pointers are never stored in guest memory.
 *  - Indirect branches go through aot_call()/aot_tailjump(), which select an
 *    already-compiled function from a static table; unknown targets are fatal.
 */
#ifndef AOT_RT_H
#define AOT_RT_H

#include <math.h>
#include <stdint.h>
#include <string.h>

#ifdef __cplusplus
extern "C" {
#endif

#define AOT_B 0x00100000u              /* guest address of original vaddr 0 */
#define AOT_IMPORT_THUNK_BASE 0x00010000u
#define AOT_JNI_THUNK_BASE 0x00020000u
#define AOT_JNI_THUNK_COUNT 240u

typedef struct aot_cpu {
    uint32_t r[16];
    uint32_t n, z, c, v;        /* APSR flags, each 0 or 1 */
    uint32_t s[32];             /* VFP single-precision registers (bit patterns) */
    uint32_t fn, fz, fc, fv;    /* FPSCR NZCV */
    /* host bookkeeping */
    uint32_t stack_lo, stack_hi;
    uint32_t depth;
    void *thread;               /* aot_thread (host) */
} aot_cpu;

typedef void (*aot_fn)(aot_cpu *c);

typedef struct aot_func_entry { uint32_t addr; aot_fn fn; const char *name; } aot_func_entry;
typedef struct aot_import_entry { uint32_t thunk; aot_fn fn; const char *name; } aot_import_entry;
typedef struct aot_segment { uint32_t vaddr; uint32_t memsz; uint32_t filesz; const uint8_t *data; } aot_segment;
typedef struct aot_reloc { uint32_t where; int type; int import_index; uint32_t symval; const char *name; } aot_reloc;
typedef struct aot_symbol { const char *name; uint32_t addr; uint32_t size; } aot_symbol;

/* generated tables (aot/generated/aot_table.c) */
extern const char aot_binary_sha256[];
extern const aot_func_entry aot_func_table[];
extern const uint32_t aot_func_count;
extern const aot_import_entry aot_import_table[];
extern const uint32_t aot_import_count;
extern const aot_segment aot_segments[];
extern const uint32_t aot_segment_count;
extern const uint32_t aot_image_end;
extern const aot_reloc aot_relocs[];
extern const uint32_t aot_reloc_count;
extern const uint32_t aot_init_array;
extern const uint32_t aot_init_array_count;
extern const aot_symbol aot_symbols[];
extern const uint32_t aot_symbol_count;

extern uint8_t *aot_mem;

/* ---------------------------------------------------------------- memory */
static inline uint32_t AOT_LD32(uint32_t a) { uint32_t v; memcpy(&v, aot_mem + a, 4); return v; }
static inline uint32_t AOT_LD16(uint32_t a) { uint16_t v; memcpy(&v, aot_mem + a, 2); return v; }
static inline uint32_t AOT_LD8(uint32_t a) { return aot_mem[a]; }
static inline void AOT_ST32(uint32_t a, uint32_t v) { memcpy(aot_mem + a, &v, 4); }
static inline void AOT_ST16(uint32_t a, uint32_t v) { uint16_t h = (uint16_t)v; memcpy(aot_mem + a, &h, 2); }
static inline void AOT_ST8(uint32_t a, uint32_t v) { aot_mem[a] = (uint8_t)v; }
static inline void *aot_host(uint32_t a) { return aot_mem + a; }

/* ---------------------------------------------------------------- integer helpers */
static inline uint32_t aot_ror(uint32_t v, unsigned n) { n &= 31u; return n ? (v >> n) | (v << (32u - n)) : v; }
static inline uint32_t aot_clz(uint32_t v) { return v ? (uint32_t)__builtin_clz(v) : 32u; }
static inline uint32_t aot_bswap32(uint32_t v) { return __builtin_bswap32(v); }

/* a + b + cin with ARM NZCV (SUB/CMP pass ~b and cin=1) */
static inline uint32_t aot_addc(uint32_t a, uint32_t b, uint32_t cin, uint32_t *n, uint32_t *z,
                                uint32_t *c, uint32_t *v)
{
    uint64_t s = (uint64_t)a + (uint64_t)b + (uint64_t)cin;
    uint32_t r = (uint32_t)s;
    *n = r >> 31;
    *z = (r == 0);
    *c = (uint32_t)(s >> 32);
    *v = ((~(a ^ b)) & (a ^ r)) >> 31;
    return r;
}

/* Register-controlled shift (ARM ARM Shift_C with amount = Rs[7:0]). */
static inline uint32_t aot_shift_reg(int type, uint32_t v, uint32_t amt, uint32_t *carry)
{
    if (amt == 0) {
        return v;
    }
    switch (type) {
    case 0: /* LSL */
        if (amt < 32) { *carry = (v >> (32 - amt)) & 1u; return v << amt; }
        *carry = (amt == 32) ? (v & 1u) : 0u;
        return 0;
    case 1: /* LSR */
        if (amt < 32) { *carry = (v >> (amt - 1)) & 1u; return v >> amt; }
        *carry = (amt == 32) ? (v >> 31) : 0u;
        return 0;
    case 2: /* ASR */
        if (amt < 32) { *carry = (v >> (amt - 1)) & 1u; return (uint32_t)((int32_t)v >> amt); }
        *carry = v >> 31;
        return (uint32_t)((int32_t)v >> 31);
    default: /* ROR */
        if ((amt & 31u) == 0) { *carry = v >> 31; return v; }
        *carry = (v >> ((amt & 31u) - 1)) & 1u;
        return aot_ror(v, amt & 31u);
    }
}

/* ---------------------------------------------------------------- VFP helpers */
static inline float aot_u2f(uint32_t u) { float f; memcpy(&f, &u, 4); return f; }
static inline uint32_t aot_f2u(float f) { uint32_t u; memcpy(&u, &f, 4); return u; }
static inline double aot_u2d(uint32_t lo, uint32_t hi)
{
    uint64_t b = ((uint64_t)hi << 32) | lo;
    double d;
    memcpy(&d, &b, 8);
    return d;
}
static inline void aot_d2u(double d, uint32_t *lo, uint32_t *hi)
{
    uint64_t b;
    memcpy(&b, &d, 8);
    *lo = (uint32_t)b;
    *hi = (uint32_t)(b >> 32);
}
/* VCMP/VCMPE -> FPSCR NZCV: less 1000, equal 0110, greater 0010, unordered 0011 */
static inline void aot_fcmp(double a, double b, uint32_t *n, uint32_t *z, uint32_t *c, uint32_t *v)
{
    if (a != a || b != b) { *n = 0; *z = 0; *c = 1; *v = 1; }
    else if (a == b) { *n = 0; *z = 1; *c = 1; *v = 0; }
    else if (a < b) { *n = 1; *z = 0; *c = 0; *v = 0; }
    else { *n = 0; *z = 0; *c = 1; *v = 0; }
}
/* VCVT to integer: round toward zero, saturate, NaN -> 0 (ARM FPToFixed) */
static inline int32_t aot_f2s32(double x)
{
    if (x != x) return 0;
    if (x >= 2147483648.0) return INT32_MAX;
    if (x <= -2147483649.0) return INT32_MIN;
    return (int32_t)x;
}
static inline uint32_t aot_f2u32(double x)
{
    if (x != x || x <= -1.0) return 0;
    if (x >= 4294967296.0) return UINT32_MAX;
    return (uint32_t)x;
}

/* ---------------------------------------------------------------- control flow */
void aot_call(aot_cpu *c, uint32_t target, uint32_t site);
void aot_tailjump(aot_cpu *c, uint32_t target, uint32_t site, uint32_t entry_lr);
void aot_unsupported(aot_cpu *c, uint32_t site, const char *what) __attribute__((noreturn));
void aot_fell_off_end(aot_cpu *c, uint32_t addr) __attribute__((noreturn));
void aot_bad_jump(aot_cpu *c, uint32_t site, uint32_t target) __attribute__((noreturn));
void aot_fatal(const char *fmt, ...) __attribute__((noreturn, format(printf, 1, 2)));
aot_fn aot_lookup_function(uint32_t addr);

#ifdef AOT_TRACE_CALLS
void aot_trace_enter(aot_cpu *c, uint32_t addr);
#define AOT_TRACE(addr) aot_trace_enter(c, addr)
#else
#define AOT_TRACE(addr) ((void)0)
#endif

#define AOT_LOCALS \
    uint32_t r0 = c->r[0], r1 = c->r[1], r2 = c->r[2], r3 = c->r[3], r4 = c->r[4], r5 = c->r[5], \
             r6 = c->r[6], r7 = c->r[7], r8 = c->r[8], r9 = c->r[9], r10 = c->r[10], r11 = c->r[11], \
             r12 = c->r[12], sp = c->r[13], lr = c->r[14]; \
    uint32_t N = c->n, Z = c->z, C = c->c, V = c->v;

#define AOT_LOCALS_VFP AOT_LOCALS \
    uint32_t s0 = c->s[0], s1 = c->s[1], s2 = c->s[2], s3 = c->s[3], s4 = c->s[4], s5 = c->s[5], \
             s6 = c->s[6], s7 = c->s[7], s8 = c->s[8], s9 = c->s[9], s10 = c->s[10], s11 = c->s[11], \
             s12 = c->s[12], s13 = c->s[13], s14 = c->s[14], s15 = c->s[15], s16 = c->s[16], \
             s17 = c->s[17], s18 = c->s[18], s19 = c->s[19], s20 = c->s[20], s21 = c->s[21], \
             s22 = c->s[22], s23 = c->s[23], s24 = c->s[24], s25 = c->s[25], s26 = c->s[26], \
             s27 = c->s[27], s28 = c->s[28], s29 = c->s[29], s30 = c->s[30], s31 = c->s[31]; \
    uint32_t FN = c->fn, FZ = c->fz, FC = c->fc, FV = c->fv;

#define AOT_SAVE_INT \
    c->r[0] = r0; c->r[1] = r1; c->r[2] = r2; c->r[3] = r3; c->r[4] = r4; c->r[5] = r5; \
    c->r[6] = r6; c->r[7] = r7; c->r[8] = r8; c->r[9] = r9; c->r[10] = r10; c->r[11] = r11; \
    c->r[12] = r12; c->r[13] = sp; c->r[14] = lr; c->n = N; c->z = Z; c->c = C; c->v = V;
#define AOT_LOAD_INT \
    r0 = c->r[0]; r1 = c->r[1]; r2 = c->r[2]; r3 = c->r[3]; r4 = c->r[4]; r5 = c->r[5]; \
    r6 = c->r[6]; r7 = c->r[7]; r8 = c->r[8]; r9 = c->r[9]; r10 = c->r[10]; r11 = c->r[11]; \
    r12 = c->r[12]; sp = c->r[13]; lr = c->r[14]; N = c->n; Z = c->z; C = c->c; V = c->v;
#define AOT_SAVE_VFP \
    c->s[0] = s0; c->s[1] = s1; c->s[2] = s2; c->s[3] = s3; c->s[4] = s4; c->s[5] = s5; c->s[6] = s6; \
    c->s[7] = s7; c->s[8] = s8; c->s[9] = s9; c->s[10] = s10; c->s[11] = s11; c->s[12] = s12; \
    c->s[13] = s13; c->s[14] = s14; c->s[15] = s15; c->s[16] = s16; c->s[17] = s17; c->s[18] = s18; \
    c->s[19] = s19; c->s[20] = s20; c->s[21] = s21; c->s[22] = s22; c->s[23] = s23; c->s[24] = s24; \
    c->s[25] = s25; c->s[26] = s26; c->s[27] = s27; c->s[28] = s28; c->s[29] = s29; c->s[30] = s30; \
    c->s[31] = s31; c->fn = FN; c->fz = FZ; c->fc = FC; c->fv = FV;
#define AOT_LOAD_VFP \
    s0 = c->s[0]; s1 = c->s[1]; s2 = c->s[2]; s3 = c->s[3]; s4 = c->s[4]; s5 = c->s[5]; s6 = c->s[6]; \
    s7 = c->s[7]; s8 = c->s[8]; s9 = c->s[9]; s10 = c->s[10]; s11 = c->s[11]; s12 = c->s[12]; \
    s13 = c->s[13]; s14 = c->s[14]; s15 = c->s[15]; s16 = c->s[16]; s17 = c->s[17]; s18 = c->s[18]; \
    s19 = c->s[19]; s20 = c->s[20]; s21 = c->s[21]; s22 = c->s[22]; s23 = c->s[23]; s24 = c->s[24]; \
    s25 = c->s[25]; s26 = c->s[26]; s27 = c->s[27]; s28 = c->s[28]; s29 = c->s[29]; s30 = c->s[30]; \
    s31 = c->s[31]; FN = c->fn; FZ = c->fz; FC = c->fc; FV = c->fv;

/* AOT_SAVE / AOT_LOAD are defined per function by the generator. */
#define AOT_CALL(fn) do { AOT_SAVE fn(c); AOT_LOAD } while (0)
#define AOT_TAILCALL(fn) do { AOT_SAVE fn(c); return; } while (0)
#define AOT_CALL_INDIRECT(t, site) do { AOT_SAVE aot_call(c, (t), (site)); AOT_LOAD } while (0)
#define AOT_JUMP(t, site) do { AOT_SAVE if ((t) == entry_lr) return; aot_tailjump(c, (t), (site), entry_lr); return; } while (0)
#define AOT_RETURN_TO(t, site) AOT_JUMP(t, site)

#ifdef __cplusplus
}
#endif
#endif /* AOT_RT_H */
