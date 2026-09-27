/*
 * Guest-side implementations of the libc / libm / C++ runtime imports of the
 * original libsnailmail.so (docs/PLATFORM_BOUNDARIES.md, analysis/native/
 * platform_imports.json). Arguments follow AAPCS softfp: r0-r3 then the guest
 * stack; doubles in (even, odd) register pairs; floats as bit patterns.
 */
#define _GNU_SOURCE
#include <errno.h>
#include <math.h>
#include <pthread.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/time.h>
#include <unistd.h>

#include "aot_host.h"

uint32_t aot_rtdata_alloc(uint32_t n);
void *aot_thread_java_ctx(void);

/* ---------------------------------------------------------------- argument access */
static inline uint32_t arg(aot_cpu *c, int i)
{
    return i < 4 ? c->r[i] : AOT_LD32(c->r[13] + 4u * (uint32_t)(i - 4));
}
static inline double argd(aot_cpu *c, int i) { return aot_u2d(arg(c, i), arg(c, i + 1)); }
static inline float argf(aot_cpu *c, int i) { return aot_u2f(arg(c, i)); }
static inline void retd(aot_cpu *c, double d) { aot_d2u(d, &c->r[0], &c->r[1]); }
static inline void retf(aot_cpu *c, float f) { c->r[0] = aot_f2u(f); }
static inline const char *gstr(uint32_t p) { return (const char *)aot_host(p); }

#define IMP(name) void aot_imp_##name(aot_cpu *c)
#define FATAL_IMP(name) \
    IMP(name) { aot_fatal("guest called unsupported import " #name " (lr %08x)", c->r[14]); }

/* ---------------------------------------------------------------- data imports */
static uint32_t g_sF;             /* bionic __sF[3], 32-bit layout, 0x54 bytes each */
static uint32_t g_stack_guard;
#define SFILE_SIZE 0x54u

uint32_t aot_import_data_addr(const char *name)
{
    if (strcmp(name, "__sF") == 0) return g_sF;
    if (strcmp(name, "__stack_chk_guard") == 0) return g_stack_guard;
    return 0;
}
FATAL_IMP(__sF)
FATAL_IMP(__stack_chk_guard)

/* ---------------------------------------------------------------- stdio handles */
#define MAX_FILES 64
static FILE *g_files[MAX_FILES];
static uint32_t g_file_guest[MAX_FILES];
static pthread_mutex_t g_io_lock = PTHREAD_MUTEX_INITIALIZER;

static uint32_t file_register(FILE *f)
{
    int i;
    pthread_mutex_lock(&g_io_lock);
    for (i = 3; i < MAX_FILES; ++i) {
        if (!g_files[i]) {
            g_files[i] = f;
            if (!g_file_guest[i]) g_file_guest[i] = aot_rtdata_alloc(16);
            pthread_mutex_unlock(&g_io_lock);
            return g_file_guest[i];
        }
    }
    pthread_mutex_unlock(&g_io_lock);
    aot_fatal("too many open guest FILEs");
}

static FILE *file_get(uint32_t g, int *slot)
{
    int i;
    if (g == g_sF + SFILE_SIZE) return stdout;
    if (g == g_sF + 2 * SFILE_SIZE) return stderr;
    for (i = 3; i < MAX_FILES; ++i) {
        if (g_files[i] && g_file_guest[i] == g) {
            if (slot) *slot = i;
            return g_files[i];
        }
    }
    aot_fatal("unknown guest FILE* %08x", g);
}

void aot_libc_init(void)
{
    g_sF = aot_rtdata_alloc(3 * SFILE_SIZE);
    g_stack_guard = aot_rtdata_alloc(4);
    AOT_ST32(g_stack_guard, 0x5AFE1234u);
}

/* ---------------------------------------------------------------- printf family */
typedef struct {
    aot_cpu *c;
    int slot;       /* AAPCS slot: 0-3 registers, 4+ stack words */
    uint32_t va;    /* va_list pointer (guest), when use_va */
    int use_va;
} argsrc;

static uint32_t next32(argsrc *s)
{
    uint32_t v;
    if (s->use_va) {
        v = AOT_LD32(s->va);
        s->va += 4;
        return v;
    }
    return arg(s->c, s->slot++);
}

static uint64_t next64(argsrc *s)
{
    uint32_t lo, hi;
    if (s->use_va) {
        s->va = (s->va + 7u) & ~7u;
        lo = AOT_LD32(s->va);
        hi = AOT_LD32(s->va + 4);
        s->va += 8;
    } else {
        if (s->slot & 1) s->slot++;
        lo = arg(s->c, s->slot);
        hi = arg(s->c, s->slot + 1);
        s->slot += 2;
    }
    return ((uint64_t)hi << 32) | lo;
}

typedef struct {
    char *p;
    size_t n, cap;
} sbuf;

static void sb_put(sbuf *b, const char *s, size_t n)
{
    if (b->n + n + 1 > b->cap) {
        size_t nc = b->cap ? b->cap * 2 : 256;
        while (nc < b->n + n + 1) nc *= 2;
        b->p = (char *)realloc(b->p, nc);
        if (!b->p) aot_fatal("out of host memory");
        b->cap = nc;
    }
    memcpy(b->p + b->n, s, n);
    b->n += n;
    b->p[b->n] = 0;
}

/* bionic-compatible formatting of guest varargs; returns host buffer.
 * The per-conversion format strings are built at run time from the guest's
 * format string (validated flag/width/precision/conversion characters only),
 * hence the local -Wformat-nonliteral suppression. */
#pragma GCC diagnostic push
#pragma GCC diagnostic ignored "-Wformat-nonliteral"
static sbuf guest_format(const char *fmt, argsrc *s)
{
    sbuf out = {0, 0, 0};
    const char *f = fmt;
    sb_put(&out, "", 0);
    while (*f) {
        char spec[64], tmp[512];
        const char *start;
        int len_l = 0, len_h = 0, n, width_star = 0, prec_star = 0, wv = 0, pv = 0;
        size_t k = 0;
        if (*f != '%') {
            const char *e = strchr(f, '%');
            size_t m = e ? (size_t)(e - f) : strlen(f);
            sb_put(&out, f, m);
            f += m;
            continue;
        }
        start = f++;
        if (*f == '%') {
            sb_put(&out, "%", 1);
            ++f;
            continue;
        }
        spec[k++] = '%';
        while (*f && strchr("-+ #0", *f)) spec[k++] = *f++;
        if (*f == '*') {
            width_star = 1;
            wv = (int32_t)next32(s);
            ++f;
            spec[k++] = '*';
        } else {
            while (*f >= '0' && *f <= '9') spec[k++] = *f++;
        }
        if (*f == '.') {
            spec[k++] = *f++;
            if (*f == '*') {
                prec_star = 1;
                pv = (int32_t)next32(s);
                ++f;
                spec[k++] = '*';
            } else {
                while (*f >= '0' && *f <= '9') spec[k++] = *f++;
            }
        }
        while (*f && strchr("hlLqjzt", *f)) {
            if (*f == 'l' || *f == 'q' || *f == 'L' || *f == 'j') len_l++;
            if (*f == 'h') len_h++;
            ++f;
        }
        if (!*f || k > 40) {
            aot_fatal("unsupported printf format \"%s\"", start);
        }
        {
            char conv = *f++;
            char fs[80];
            int ll = len_l >= 2;
            spec[k] = 0; /* prefix: %, flags, width, precision */
#define MKSPEC(lenmod, cv) snprintf(fs, sizeof fs, "%s%s%c", spec, lenmod, cv)
#define FMT1(v) \
    (width_star && prec_star ? snprintf(tmp, sizeof tmp, fs, wv, pv, v) \
     : width_star            ? snprintf(tmp, sizeof tmp, fs, wv, v) \
     : prec_star             ? snprintf(tmp, sizeof tmp, fs, pv, v) \
                             : snprintf(tmp, sizeof tmp, fs, v))
            switch (conv) {
            case 'd':
            case 'i':
                if (ll) {
                    MKSPEC("ll", conv);
                    n = FMT1((long long)(int64_t)next64(s));
                } else {
                    int32_t v = (int32_t)next32(s);
                    if (len_h == 1) v = (int16_t)v;
                    if (len_h >= 2) v = (int8_t)v;
                    MKSPEC("", conv);
                    n = FMT1((int)v);
                }
                break;
            case 'u':
            case 'o':
            case 'x':
            case 'X':
                if (ll) {
                    MKSPEC("ll", conv);
                    n = FMT1((unsigned long long)next64(s));
                } else {
                    uint32_t v = next32(s);
                    if (len_h == 1) v = (uint16_t)v;
                    if (len_h >= 2) v = (uint8_t)v;
                    MKSPEC("", conv);
                    n = FMT1((unsigned)v);
                }
                break;
            case 'c':
                MKSPEC("", 'c');
                n = FMT1((int)(int32_t)next32(s));
                break;
            case 's': {
                uint32_t p = next32(s);
                MKSPEC("", 's');
                n = FMT1(p ? gstr(p) : "(null)");
                break;
            }
            case 'p':
                sb_put(&out, "0x", 2);
                MKSPEC("", 'x');
                n = FMT1((unsigned)next32(s));
                break;
            case 'f':
            case 'F':
            case 'e':
            case 'E':
            case 'g':
            case 'G':
            case 'a':
            case 'A': {
                uint64_t b = next64(s);
                double d;
                memcpy(&d, &b, 8);
                MKSPEC("", conv);
                n = FMT1(d);
                break;
            }
            default:
                aot_fatal("unsupported printf conversion %%%c in \"%s\"", conv, fmt);
            }
#undef FMT1
#undef MKSPEC
            if (n < 0) n = 0;
            if ((size_t)n >= sizeof tmp) aot_fatal("printf field too long");
            sb_put(&out, tmp, (size_t)n);
        }
    }
    return out;
}
#pragma GCC diagnostic pop

static uint32_t emit_to_guest(uint32_t dst, sbuf *b)
{
    memcpy(aot_host(dst), b->p, b->n + 1);
    free(b->p);
    return (uint32_t)b->n;
}

static void emit_log(sbuf *b)
{
    /* game debug output (the shipped build prints nothing: wprintf is empty) */
    aot_log(3, "guest: %s", b->p);
    free(b->p);
}

IMP(sprintf)
{
    argsrc s = {c, 2, 0, 0};
    sbuf b = guest_format(gstr(c->r[1]), &s);
    c->r[0] = emit_to_guest(c->r[0], &b);
}
IMP(vsprintf)
{
    argsrc s = {c, 0, c->r[2], 1};
    sbuf b = guest_format(gstr(c->r[1]), &s);
    c->r[0] = emit_to_guest(c->r[0], &b);
}
IMP(printf)
{
    argsrc s = {c, 1, 0, 0};
    sbuf b = guest_format(gstr(c->r[0]), &s);
    uint32_t n = (uint32_t)b.n;
    emit_log(&b);
    c->r[0] = n;
}
IMP(fprintf)
{
    argsrc s = {c, 2, 0, 0};
    sbuf b = guest_format(gstr(c->r[1]), &s);
    FILE *f = file_get(c->r[0], NULL);
    uint32_t n = (uint32_t)b.n;
    if (f == stdout || f == stderr) {
        emit_log(&b);
    } else {
        fwrite(b.p, 1, b.n, f);
        free(b.p);
    }
    c->r[0] = n;
}
IMP(putchar)
{
    aot_log(3, "guest putchar %c", (char)c->r[0]);
}
IMP(fputc)
{
    FILE *f = file_get(c->r[1], NULL);
    if (f != stdout && f != stderr) {
        c->r[0] = (uint32_t)fputc((int)(c->r[0] & 0xFF), f);
    }
}

/* ---------------------------------------------------------------- stdio */
IMP(dup)
{
    int r = dup((int)c->r[0]);
    c->r[0] = (uint32_t)r;
}
IMP(fdopen)
{
    FILE *f = fdopen((int)c->r[0], gstr(c->r[1]));
    c->r[0] = f ? file_register(f) : 0;
}
IMP(fopen)
{
    const char *path = gstr(c->r[0]);
    const char *dir = aot_cfg->files_dir;
    char full[1024];
    FILE *f = NULL;
    if (path[0] != '/' && dir && strstr(path, "..") == NULL) {
        snprintf(full, sizeof full, "%s/%s", dir, path);
        f = fopen(full, gstr(c->r[1]));
    }
    aot_log(5, "guest fopen(\"%s\", \"%s\") -> %s", path, gstr(c->r[1]), f ? "ok" : "refused/failed");
    c->r[0] = f ? file_register(f) : 0;
}
IMP(fclose)
{
    int slot = -1;
    FILE *f = file_get(c->r[0], &slot);
    if (slot < 0) {
        c->r[0] = 0;
        return;
    }
    pthread_mutex_lock(&g_io_lock);
    g_files[slot] = NULL;
    pthread_mutex_unlock(&g_io_lock);
    c->r[0] = (uint32_t)fclose(f);
}
IMP(fread)
{
    FILE *f = file_get(c->r[3], NULL);
    c->r[0] = (uint32_t)fread(aot_host(c->r[0]), c->r[1], c->r[2], f);
}
IMP(fwrite)
{
    FILE *f = file_get(c->r[3], NULL);
    c->r[0] = (uint32_t)fwrite(aot_host(c->r[0]), c->r[1], c->r[2], f);
}
IMP(fseek)
{
    FILE *f = file_get(c->r[0], NULL);
    c->r[0] = (uint32_t)fseeko(f, (off_t)(int32_t)c->r[1], (int)c->r[2]);
}
IMP(ftell)
{
    FILE *f = file_get(c->r[0], NULL);
    off_t p = ftello(f);
    c->r[0] = p > 0x7FFFFFFF ? 0xFFFFFFFFu : (uint32_t)p;
}
IMP(gettimeofday)
{
    struct timeval tv;
    gettimeofday(&tv, NULL);
    if (c->r[0]) {
        AOT_ST32(c->r[0], (uint32_t)tv.tv_sec);
        AOT_ST32(c->r[0] + 4, (uint32_t)tv.tv_usec);
    }
    c->r[0] = 0;
}
IMP(getcwd)
{
    const char *d = aot_cfg->files_dir ? aot_cfg->files_dir : "/";
    size_t n = strlen(d);
    if (n + 1 > c->r[1]) {
        c->r[0] = 0;
        return;
    }
    memcpy(aot_host(c->r[0]), d, n + 1);
}
IMP(chdir)
{
    aot_log(5, "guest chdir(\"%s\") ignored", gstr(c->r[0]));
    c->r[0] = 0;
}
IMP(exit) { aot_fatal("guest called exit(%d)", (int)c->r[0]); }
IMP(abort) { aot_fatal("guest called abort() (lr %08x)", c->r[14]); }

/* ---------------------------------------------------------------- memory / strings */
IMP(malloc) { c->r[0] = aot_malloc(c->r[0]); }
IMP(free) { aot_free(c->r[0]); }
IMP(_Znwj)
{
    uint32_t p = aot_malloc(c->r[0]);
    if (!p) aot_fatal("operator new(%u) failed", c->r[0]);
    c->r[0] = p;
}
IMP(memcpy) { memmove(aot_host(c->r[0]), aot_host(c->r[1]), c->r[2]); }
IMP(memmove) { memmove(aot_host(c->r[0]), aot_host(c->r[1]), c->r[2]); }
IMP(memset) { memset(aot_host(c->r[0]), (int)(c->r[1] & 0xFF), c->r[2]); }
IMP(strlen) { c->r[0] = (uint32_t)strlen(gstr(c->r[0])); }
IMP(strcpy)
{
    size_t n = strlen(gstr(c->r[1]));
    memmove(aot_host(c->r[0]), aot_host(c->r[1]), n + 1);
}
IMP(strcat)
{
    size_t d = strlen(gstr(c->r[0]));
    size_t n = strlen(gstr(c->r[1]));
    memmove(aot_host(c->r[0] + (uint32_t)d), aot_host(c->r[1]), n + 1);
}
IMP(atoi) { c->r[0] = (uint32_t)atoi(gstr(c->r[0])); }
IMP(strtod)
{
    char *end = NULL;
    const char *s = gstr(c->r[0]);
    double d = strtod(s, &end);
    if (c->r[1]) AOT_ST32(c->r[1], c->r[0] + (uint32_t)(end - s));
    retd(c, d);
}

/* ---------------------------------------------------------------- libm (softfp) */
IMP(sin) { retd(c, sin(argd(c, 0))); }
IMP(cos) { retd(c, cos(argd(c, 0))); }
IMP(acos) { retd(c, acos(argd(c, 0))); }
IMP(atan) { retd(c, atan(argd(c, 0))); }
IMP(exp) { retd(c, exp(argd(c, 0))); }
IMP(floor) { retd(c, floor(argd(c, 0))); }
IMP(sqrt) { retd(c, sqrt(argd(c, 0))); }
IMP(pow) { retd(c, pow(argd(c, 0), argd(c, 2))); }
IMP(floorf) { retf(c, floorf(argf(c, 0))); }
IMP(tanf) { retf(c, tanf(argf(c, 0))); }

/* ---------------------------------------------------------------- rand48
 * POSIX 48-bit LCG: X' = (a*X + c) mod 2^48, a = 0x5DEECE66D, c = 0xB,
 * lrand48 = X' >> 17. Default state when srand48 was never called: bionic
 * (2011, BSD-derived) uses X = 0x1234ABCD330E (__rand48_seed) - HYPOTHESIS,
 * see docs/ABI_PORTING.md; glibc would start from 0. */
static uint64_t g_x48 = 0x1234ABCD330Eull;
static pthread_mutex_t g_rand_lock = PTHREAD_MUTEX_INITIALIZER;
IMP(srand48)
{
    pthread_mutex_lock(&g_rand_lock);
    g_x48 = (((uint64_t)c->r[0]) << 16 | 0x330Eu) & 0xFFFFFFFFFFFFull;
    pthread_mutex_unlock(&g_rand_lock);
}
IMP(lrand48)
{
    pthread_mutex_lock(&g_rand_lock);
    g_x48 = (0x5DEECE66Dull * g_x48 + 0xBu) & 0xFFFFFFFFFFFFull;
    c->r[0] = (uint32_t)(g_x48 >> 17);
    pthread_mutex_unlock(&g_rand_lock);
}

/* ---------------------------------------------------------------- qsort
 * BSD (Bentley-McIlroy) qsort as in bionic, operating on guest memory and
 * calling the guest comparator with guest addresses. */
typedef struct {
    aot_cpu *c;
    uint32_t cmp;
    uint32_t es;
} qctx;

static int qcmp(qctx *q, uint32_t a, uint32_t b)
{
    uint32_t args[2] = {a, b};
    return (int32_t)aot_invoke(q->c, q->cmp, args, 2, NULL);
}
static void qswap(qctx *q, uint32_t a, uint32_t b, uint32_t n)
{
    uint8_t *pa = aot_host(a), *pb = aot_host(b);
    (void)q;
    while (n--) {
        uint8_t t = *pa;
        *pa++ = *pb;
        *pb++ = t;
    }
}
static uint32_t qmed3(qctx *q, uint32_t a, uint32_t b, uint32_t c)
{
    return qcmp(q, a, b) < 0 ? (qcmp(q, b, c) < 0 ? b : (qcmp(q, a, c) < 0 ? c : a))
                             : (qcmp(q, b, c) > 0 ? b : (qcmp(q, a, c) < 0 ? a : c));
}
static void bsd_qsort(qctx *q, uint32_t a, uint32_t n)
{
    uint32_t es = q->es, pa, pb, pc, pd, pl, pm, pn, d, r, swap_cnt;
    int cmp_result;
loop:
    swap_cnt = 0;
    if (n < 7) {
        for (pm = a + es; pm < a + n * es; pm += es)
            for (pl = pm; pl > a && qcmp(q, pl - es, pl) > 0; pl -= es) qswap(q, pl, pl - es, es);
        return;
    }
    pm = a + (n / 2) * es;
    if (n > 7) {
        pl = a;
        pn = a + (n - 1) * es;
        if (n > 40) {
            d = (n / 8) * es;
            pl = qmed3(q, pl, pl + d, pl + 2 * d);
            pm = qmed3(q, pm - d, pm, pm + d);
            pn = qmed3(q, pn - 2 * d, pn - d, pn);
        }
        pm = qmed3(q, pl, pm, pn);
    }
    qswap(q, a, pm, es);
    pa = pb = a + es;
    pc = pd = a + (n - 1) * es;
    for (;;) {
        while (pb <= pc && (cmp_result = qcmp(q, pb, a)) <= 0) {
            if (cmp_result == 0) {
                swap_cnt = 1;
                qswap(q, pa, pb, es);
                pa += es;
            }
            pb += es;
        }
        while (pb <= pc && (cmp_result = qcmp(q, pc, a)) >= 0) {
            if (cmp_result == 0) {
                swap_cnt = 1;
                qswap(q, pc, pd, es);
                pd -= es;
            }
            pc -= es;
        }
        if (pb > pc) break;
        qswap(q, pb, pc, es);
        swap_cnt = 1;
        pb += es;
        pc -= es;
    }
    if (swap_cnt == 0) { /* switch to insertion sort */
        for (pm = a + es; pm < a + n * es; pm += es)
            for (pl = pm; pl > a && qcmp(q, pl - es, pl) > 0; pl -= es) qswap(q, pl, pl - es, es);
        return;
    }
    pn = a + n * es;
    r = (pa - a) < (pb - pa) ? (pa - a) : (pb - pa);
    if (r > 0) qswap(q, a, pb - r, r);
    r = (pd - pc) < (pn - pd - es) ? (pd - pc) : (pn - pd - es);
    if (r > 0) qswap(q, pb, pn - r, r);
    if ((r = pb - pa) > es) bsd_qsort(q, a, r / es);
    if ((r = pd - pc) > es) {
        a = pn - r;
        n = r / es;
        goto loop;
    }
}
IMP(qsort)
{
    qctx q = {c, c->r[3], c->r[2]};
    uint32_t base = c->r[0], n = c->r[1];
    if (q.es == 0 || n < 2) return;
    bsd_qsort(&q, base, n);
}

/* ---------------------------------------------------------------- C++ runtime */
static pthread_mutex_t g_guard_lock = PTHREAD_MUTEX_INITIALIZER;
IMP(__cxa_guard_acquire)
{
    pthread_mutex_lock(&g_guard_lock);
    c->r[0] = (AOT_LD8(c->r[0]) & 1u) ? 0u : 1u;
    pthread_mutex_unlock(&g_guard_lock);
}
IMP(__cxa_guard_release)
{
    pthread_mutex_lock(&g_guard_lock);
    AOT_ST32(c->r[0], 1u);
    pthread_mutex_unlock(&g_guard_lock);
}
IMP(__stack_chk_fail) { aot_fatal("guest stack protector tripped (lr %08x)", c->r[14]); }
FATAL_IMP(__cxa_begin_cleanup)
FATAL_IMP(__cxa_call_unexpected)
FATAL_IMP(__cxa_type_match)
FATAL_IMP(__gnu_Unwind_Find_exidx)
