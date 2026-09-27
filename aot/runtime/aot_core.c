/*
 * AOT runtime core: guest address space, image loading and relocation,
 * static dispatch, per-thread CPU state, re-entrant guest calls, object
 * handles, guest heap and diagnostics. See aot_rt.h / aot_host.h.
 */
#define _GNU_SOURCE
#include <errno.h>
#include <pthread.h>
#include <signal.h>
#include <stdarg.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/mman.h>
#include <unistd.h>

#include "aot_host.h"

uint8_t *aot_mem;
const aot_config *aot_cfg;
uint64_t aot_calls_executed;

static aot_config g_cfg;
static int g_inited;
static pthread_mutex_t g_lock = PTHREAD_MUTEX_INITIALIZER;
static size_t g_page;
static const uint64_t k_reserve = (1ull << 32) + (1ull << 20); /* 4 GiB + slack for straddling accesses */

/* ---------------------------------------------------------------- diagnostics */
void aot_log(int level, const char *fmt, ...)
{
    char buf[1024];
    va_list ap;
    va_start(ap, fmt);
    vsnprintf(buf, sizeof buf, fmt, ap);
    va_end(ap);
    if (aot_cfg && aot_cfg->log) {
        aot_cfg->log(level, buf);
    } else {
        fprintf(stderr, "[aot:%d] %s\n", level, buf);
    }
}

void aot_fatal(const char *fmt, ...)
{
    char buf[1024];
    va_list ap;
    va_start(ap, fmt);
    vsnprintf(buf, sizeof buf, fmt, ap);
    va_end(ap);
    aot_log(7, "FATAL: %s", buf);
    abort();
}

void aot_unsupported(aot_cpu *c, uint32_t site, const char *what)
{
    (void)c;
    aot_fatal("reached untranslatable instruction at guest %08x (v7a:%#x): %s", site, site - AOT_B, what);
}

void aot_fell_off_end(aot_cpu *c, uint32_t addr)
{
    (void)c;
    aot_fatal("execution fell off the end of a function at guest %08x (v7a:%#x)", addr, addr - AOT_B);
}

void aot_bad_jump(aot_cpu *c, uint32_t site, uint32_t target)
{
    (void)c;
    aot_fatal("computed jump at v7a:%#x to %08x is not a known table target", site - AOT_B, target);
}

#ifdef AOT_TRACE_CALLS
void aot_trace_enter(aot_cpu *c, uint32_t addr)
{
    ++aot_calls_executed;
    if (aot_cfg && aot_cfg->trace) {
        fprintf(aot_cfg->trace, "F %08x r0=%08x r1=%08x r2=%08x r3=%08x sp=%08x\n", addr - AOT_B, c->r[0], c->r[1],
                c->r[2], c->r[3], c->r[13]);
    }
}
#endif

static void fault_handler(int sig, siginfo_t *si, void *uc)
{
    (void)uc;
    uint8_t *p = (uint8_t *)si->si_addr;
    char msg[160];
    int n;
    if (aot_mem && p >= aot_mem && p < aot_mem + k_reserve) {
        n = snprintf(msg, sizeof msg, "[aot] FATAL: guest memory fault (signal %d) at guest address %08x\n", sig,
                     (unsigned)(p - aot_mem));
    } else {
        n = snprintf(msg, sizeof msg, "[aot] FATAL: host fault (signal %d) at %p\n", sig, si->si_addr);
    }
    if (n > 0 && write(2, msg, (size_t)n) < 0) {
        /* nothing more we can do */
    }
    signal(sig, SIG_DFL);
    raise(sig);
}

void aot_install_fault_handler(void)
{
    struct sigaction sa;
    memset(&sa, 0, sizeof sa);
    sa.sa_sigaction = fault_handler;
    sa.sa_flags = SA_SIGINFO;
    sigaction(SIGSEGV, &sa, NULL);
    sigaction(SIGBUS, &sa, NULL);
}

/* ---------------------------------------------------------------- memory */
static void commit(uint32_t lo, uint64_t hi)
{
    uintptr_t a = ((uintptr_t)aot_mem + lo) & ~(uintptr_t)(g_page - 1);
    uintptr_t b = ((uintptr_t)aot_mem + (uintptr_t)hi + g_page - 1) & ~(uintptr_t)(g_page - 1);
    if (mprotect((void *)a, b - a, PROT_READ | PROT_WRITE) != 0) {
        aot_fatal("cannot commit guest memory %08x-%08llx: %s", lo, (unsigned long long)hi, strerror(errno));
    }
}

/* runtime data bump allocator (never freed) */
static uint32_t g_rtdata_next = AOT_RTDATA_BASE;
static uint32_t rtdata_alloc(uint32_t n)
{
    uint32_t p;
    pthread_mutex_lock(&g_lock);
    p = (g_rtdata_next + 15u) & ~15u;
    if (p + n > AOT_RTDATA_BASE + AOT_RTDATA_SIZE) {
        pthread_mutex_unlock(&g_lock);
        aot_fatal("runtime data area exhausted");
    }
    g_rtdata_next = p + n;
    pthread_mutex_unlock(&g_lock);
    memset(aot_mem + p, 0, n);
    return p;
}
uint32_t aot_rtdata_alloc(uint32_t n) { return rtdata_alloc(n); }

/* Guest heap: power-of-two size classes with per-class LIFO free lists.
 * Deterministic (same request sequence -> same addresses), 8-byte aligned
 * user pointers (as bionic's dlmalloc), header of 8 bytes before each block. */
#define HEAP_MAGIC 0xA07A0000u
static uint32_t g_heap_top = AOT_HEAP_BASE;
static uint32_t g_heap_committed = AOT_HEAP_BASE;
static uint32_t g_free[32];

uint32_t aot_malloc(uint32_t size)
{
    uint64_t need = (uint64_t)size + 8u;
    unsigned k = 4;
    uint32_t b;
    while ((1ull << k) < need) {
        ++k;
    }
    if (k > 30) {
        return 0;
    }
    pthread_mutex_lock(&g_lock);
    if (g_free[k]) {
        b = g_free[k];
        g_free[k] = AOT_LD32(b + 8u);
    } else {
        if ((uint64_t)g_heap_top + (1ull << k) > AOT_HEAP_END) {
            pthread_mutex_unlock(&g_lock);
            return 0;
        }
        b = g_heap_top;
        g_heap_top += (uint32_t)(1u << k);
        if (g_heap_top > g_heap_committed) {
            uint32_t newc = (g_heap_top + 0xFFFFFu) & ~0xFFFFFu;
            commit(g_heap_committed, newc);
            g_heap_committed = newc;
        }
    }
    AOT_ST32(b, HEAP_MAGIC | k);
    AOT_ST32(b + 4u, size);
    pthread_mutex_unlock(&g_lock);
    return b + 8u;
}

void aot_free(uint32_t p)
{
    uint32_t b, hdr, k;
    if (p == 0) {
        return;
    }
    if (p < AOT_HEAP_BASE + 8u || p >= g_heap_top) {
        aot_fatal("free() of non-heap guest pointer %08x", p);
    }
    b = p - 8u;
    hdr = AOT_LD32(b);
    if ((hdr & 0xFFFF0000u) != HEAP_MAGIC) {
        aot_fatal("free() of corrupt or foreign block %08x (header %08x)", p, hdr);
    }
    k = hdr & 0xFFu;
    pthread_mutex_lock(&g_lock);
    AOT_ST32(b, HEAP_MAGIC | 0x8000u | k); /* mark free */
    AOT_ST32(p, g_free[k]);
    g_free[k] = b;
    pthread_mutex_unlock(&g_lock);
}

uint32_t aot_heap_block_size(uint32_t p)
{
    return AOT_LD32(p - 4u);
}

/* ---------------------------------------------------------------- dispatch */
aot_fn aot_lookup_function(uint32_t addr)
{
    uint32_t lo = 0, hi = aot_func_count;
    while (lo < hi) {
        uint32_t mid = (lo + hi) / 2;
        uint32_t a = aot_func_table[mid].addr;
        if (a == addr) {
            return aot_func_table[mid].fn;
        }
        if (a < addr) {
            lo = mid + 1;
        } else {
            hi = mid;
        }
    }
    if (addr >= AOT_IMPORT_THUNK_BASE && addr < AOT_IMPORT_THUNK_BASE + 16u * aot_import_count &&
        ((addr - AOT_IMPORT_THUNK_BASE) & 15u) == 0) {
        return aot_import_table[(addr - AOT_IMPORT_THUNK_BASE) / 16u].fn;
    }
    return NULL;
}

void aot_call(aot_cpu *c, uint32_t target, uint32_t site)
{
    aot_fn fn = aot_lookup_function(target);
    if (fn) {
        fn(c);
        return;
    }
    if (target >= AOT_JNI_THUNK_BASE && target < AOT_JNI_THUNK_BASE + 4u * AOT_JNI_THUNK_COUNT &&
        (target & 3u) == 0) {
        aot_jni_dispatch(c, (target - AOT_JNI_THUNK_BASE) / 4u);
        return;
    }
    aot_fatal("indirect call at v7a:%#x to unknown target %08x", site - AOT_B, target);
}

void aot_tailjump(aot_cpu *c, uint32_t target, uint32_t site, uint32_t entry_lr)
{
    aot_fn fn = aot_lookup_function(target);
    if (fn) {
        fn(c);
        return;
    }
    if (target >= AOT_JNI_THUNK_BASE && target < AOT_JNI_THUNK_BASE + 4u * AOT_JNI_THUNK_COUNT) {
        aot_jni_dispatch(c, (target - AOT_JNI_THUNK_BASE) / 4u);
        return;
    }
    aot_fatal("jump at v7a:%#x to %08x which is neither the return address %08x nor a function", site - AOT_B,
              target, entry_lr);
}

uint32_t aot_symbol_addr(const char *name)
{
    for (uint32_t i = 0; i < aot_symbol_count; ++i) {
        if (strcmp(aot_symbols[i].name, name) == 0) {
            return aot_symbols[i].addr;
        }
    }
    return 0;
}

/* ---------------------------------------------------------------- threads */
static __thread aot_cpu *t_cpu;
static __thread void *t_java_ctx;
static __thread uint32_t t_env;
static uint32_t g_next_stack = AOT_STACK_BASE;
uint32_t aot_jni_new_env(void);

aot_cpu *aot_thread_enter(void *java_ctx)
{
    if (!g_inited) {
        aot_fatal("aot_thread_enter before aot_init");
    }
    if (!t_cpu) {
        aot_cpu *c = (aot_cpu *)calloc(1, sizeof *c);
        uint32_t lo;
        if (!c) {
            aot_fatal("out of host memory");
        }
        pthread_mutex_lock(&g_lock);
        lo = g_next_stack;
        g_next_stack += AOT_STACK_SIZE;
        pthread_mutex_unlock(&g_lock);
        if (g_next_stack > 0xF0000000u) {
            aot_fatal("too many guest threads");
        }
        /* 64 KiB guard at the bottom stays uncommitted: overflow faults. */
        commit(lo + 0x10000u, (uint64_t)lo + AOT_STACK_SIZE);
        c->stack_lo = lo + 0x10000u;
        c->stack_hi = lo + AOT_STACK_SIZE;
        c->r[13] = c->stack_hi;
        t_cpu = c;
        t_env = aot_jni_new_env();
    }
    t_java_ctx = java_ctx;
    return t_cpu;
}

void *aot_thread_java_ctx(void) { return t_java_ctx; }
uint32_t aot_thread_guest_env(void) { return t_env; }

uint32_t aot_invoke(aot_cpu *c, uint32_t addr, const uint32_t *args, int nargs, uint32_t *hi)
{
    aot_cpu saved = *c;
    aot_fn fn = aot_lookup_function(addr);
    uint32_t sp, r0, r1;
    int i, nstack = nargs > 4 ? nargs - 4 : 0;
    if (!fn) {
        aot_fatal("aot_invoke of unknown guest function %08x", addr);
    }
    sp = c->depth ? c->r[13] : c->stack_hi;
    sp = (sp - 4u * (uint32_t)nstack) & ~7u;
    for (i = 0; i < nstack; ++i) {
        AOT_ST32(sp + 4u * (uint32_t)i, args[4 + i]);
    }
    for (i = 0; i < 4; ++i) {
        c->r[i] = i < nargs ? args[i] : 0;
    }
    c->r[13] = sp;
    c->r[14] = AOT_RETURN_MAGIC;
    c->depth++;
    fn(c);
    r0 = c->r[0];
    r1 = c->r[1];
    if (c->r[13] != sp) {
        aot_fatal("guest function %08x returned with sp %08x, expected %08x", addr, c->r[13], sp);
    }
    *c = saved;
    if (hi) {
        *hi = r1;
    }
    return r0;
}

/* ---------------------------------------------------------------- handles */
typedef struct {
    void *obj;
    int used;
    int owned;
} handle_slot;
static handle_slot g_handles[AOT_HANDLE_MAX / 4u];

uint32_t aot_handle_new(void *obj, int owned)
{
    uint32_t i;
    if (!obj) {
        return 0;
    }
    pthread_mutex_lock(&g_lock);
    for (i = 1; i < AOT_HANDLE_MAX / 4u; ++i) {
        if (!g_handles[i].used) {
            g_handles[i].used = 1;
            g_handles[i].obj = obj;
            g_handles[i].owned = owned;
            pthread_mutex_unlock(&g_lock);
            return AOT_HANDLE_BASE + 4u * i;
        }
    }
    pthread_mutex_unlock(&g_lock);
    aot_fatal("guest object handle table full");
}

void *aot_handle_get(uint32_t h)
{
    uint32_t i;
    if (h == 0) {
        return NULL;
    }
    if (h < AOT_HANDLE_BASE || h >= AOT_HANDLE_BASE + AOT_HANDLE_MAX || (h & 3u)) {
        aot_fatal("invalid guest object handle %08x", h);
    }
    i = (h - AOT_HANDLE_BASE) / 4u;
    if (!g_handles[i].used) {
        aot_fatal("stale guest object handle %08x", h);
    }
    return g_handles[i].obj;
}

uint32_t aot_handle_for_incoming(void *java_ctx, void *obj)
{
    uint32_t i;
    const aot_java_ops *j = aot_cfg->java;
    if (!obj) {
        return 0;
    }
    for (i = 1; i < AOT_HANDLE_MAX / 4u; ++i) {
        if (g_handles[i].used && g_handles[i].owned == 2 && j->is_same_object(java_ctx, g_handles[i].obj, obj)) {
            return AOT_HANDLE_BASE + 4u * i;
        }
    }
    /* owned == 2: an object that entered from the host; kept for the process
     * lifetime because the original caches such references (gJavaObj). */
    return aot_handle_new(j->new_global_ref(java_ctx, obj), 2);
}

void aot_handle_release(void *java_ctx, uint32_t h)
{
    uint32_t i;
    if (h == 0) {
        return;
    }
    aot_handle_get(h);
    i = (h - AOT_HANDLE_BASE) / 4u;
    if (g_handles[i].owned == 2) {
        return; /* host-entered objects are never released by the guest */
    }
    if (g_handles[i].owned) {
        aot_cfg->java->delete_ref(java_ctx, g_handles[i].obj);
    }
    pthread_mutex_lock(&g_lock);
    g_handles[i].used = 0;
    g_handles[i].obj = NULL;
    pthread_mutex_unlock(&g_lock);
}

/* ---------------------------------------------------------------- init */
static uint32_t import_address(int idx, const char *name)
{
    uint32_t d = aot_import_data_addr(name);
    if (d) {
        return d;
    }
    if (idx < 0 || (uint32_t)idx >= aot_import_count) {
        aot_fatal("relocation against unknown import %s", name);
    }
    return AOT_IMPORT_THUNK_BASE + 16u * (uint32_t)idx;
}

int aot_initialized(void) { return g_inited; }

int aot_init(const aot_config *cfg)
{
    uint32_t i;
    aot_cpu *c;
    if (g_inited) {
        return 0;
    }
    g_cfg = *cfg;
    aot_cfg = &g_cfg;
    g_page = (size_t)sysconf(_SC_PAGESIZE);
    if (sizeof(void *) < 8) {
        aot_fatal("the AOT runtime needs a 64-bit host (4 GiB guest reservation)");
    }
    aot_mem = (uint8_t *)mmap(NULL, (size_t)k_reserve, PROT_NONE, MAP_PRIVATE | MAP_ANONYMOUS | MAP_NORESERVE,
                              -1, 0);
    if (aot_mem == MAP_FAILED) {
        aot_fatal("cannot reserve 4 GiB guest address space: %s", strerror(errno));
    }
    /* image */
    commit(AOT_B, aot_image_end);
    for (i = 0; i < aot_segment_count; ++i) {
        memcpy(aot_mem + aot_segments[i].vaddr, aot_segments[i].data, aot_segments[i].filesz);
    }
    commit(AOT_RTDATA_BASE, AOT_RTDATA_BASE + AOT_RTDATA_SIZE);
    aot_libc_init();
    aot_jni_init_tables();
    /* relocations (REL: implicit addends in place) */
    for (i = 0; i < aot_reloc_count; ++i) {
        const aot_reloc *r = &aot_relocs[i];
        uint32_t S = r->import_index >= 0 ? import_address(r->import_index, r->name) : r->symval;
        switch (r->type) {
        case 23: /* R_ARM_RELATIVE */
            AOT_ST32(r->where, AOT_LD32(r->where) + AOT_B);
            break;
        case 2: /* R_ARM_ABS32 */
            AOT_ST32(r->where, AOT_LD32(r->where) + S);
            break;
        case 21: /* R_ARM_GLOB_DAT */
        case 22: /* R_ARM_JUMP_SLOT */
            AOT_ST32(r->where, S);
            break;
        default:
            aot_fatal("unsupported relocation type %d at %08x", r->type, r->where);
        }
    }
    g_inited = 1;
    aot_log(4, "guest image %s loaded: %u functions, %u imports, %u relocations", aot_binary_sha256,
            aot_func_count, aot_import_count, aot_reloc_count);
    /* INIT_ARRAY, as the dynamic linker ran it at System.loadLibrary */
    c = aot_thread_enter(NULL);
    for (i = 0; i < aot_init_array_count; ++i) {
        uint32_t fn = AOT_LD32(aot_init_array + 4u * i);
        if (fn != 0 && fn != 0xFFFFFFFFu) {
            aot_invoke(c, fn, NULL, 0, NULL);
        }
    }
    return 0;
}
