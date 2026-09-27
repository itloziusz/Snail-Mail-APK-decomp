/*
 * snailmail_ref: REFERENCE runner (analysis only, never shipped).
 *
 * Runs the game with the ORIGINAL ARM32 instructions of libsnailmail.so
 * (v7a) executed by the Unicorn CPU emulator, against exactly the same C
 * runtime, Java emulation, input script and virtual clock as the AOT build
 * (host/main.c). Only the CPU differs:
 *   - guest memory is the runtime's own reservation, mapped into Unicorn by
 *     pointer at identical guest addresses; the image bytes there are the
 *     original code/data after the same relocations;
 *   - import thunks (0x10000+16*i) and JNI thunks (0x20000+4*i) are pages of
 *     "bx lr" in Unicorn; a code hook there calls the runtime's C shims;
 *   - aot_invoke() is redirected here (aot_invoke_backend).
 * Comparing --gltrace outputs of snailmail_host --headless and snailmail_ref
 * therefore checks the translation against the original machine code.
 */
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unicorn/unicorn.h>

#include "aot_host.h"

int sm_host_run(int argc, char **argv);
void aot_jni_dispatch(aot_cpu *c, uint32_t index);

static uc_engine *g_uc;
static aot_cpu *g_cur;
static uint64_t g_blocks, g_insns, g_emu_starts;

/* SM_REF_COUNT=1: count executed basic blocks/instructions of original code
 * (proof that Unicorn, not translated code, ran; slows the run down). */
static void block_hook(uc_engine *uc, uint64_t addr, uint32_t size, void *ud)
{
    (void)uc;
    (void)addr;
    (void)ud;
    g_blocks++;
    g_insns += size / 4u;
}

static const int k_regs[15] = {UC_ARM_REG_R0,  UC_ARM_REG_R1,  UC_ARM_REG_R2, UC_ARM_REG_R3, UC_ARM_REG_R4,
                               UC_ARM_REG_R5,  UC_ARM_REG_R6,  UC_ARM_REG_R7, UC_ARM_REG_R8, UC_ARM_REG_R9,
                               UC_ARM_REG_R10, UC_ARM_REG_R11, UC_ARM_REG_R12, UC_ARM_REG_SP, UC_ARM_REG_LR};

static void check(uc_err e, const char *what)
{
    if (e != UC_ERR_OK) {
        fprintf(stderr, "[ref] unicorn %s: %s\n", what, uc_strerror(e));
        exit(4);
    }
}

static void sync_in(aot_cpu *c)
{
    for (int i = 0; i < 15; ++i) {
        uint32_t v;
        uc_reg_read(g_uc, k_regs[i], &v);
        c->r[i] = v;
    }
}
static void sync_out(aot_cpu *c)
{
    for (int i = 0; i < 15; ++i) {
        uint32_t v = c->r[i];
        uc_reg_write(g_uc, k_regs[i], &v);
    }
}

static void thunk_hook(uc_engine *uc, uint64_t addr, uint32_t size, void *ud)
{
    aot_cpu *c = g_cur;
    uint32_t a = (uint32_t)addr;
    (void)uc;
    (void)size;
    (void)ud;
    sync_in(c);
    if (a >= AOT_IMPORT_THUNK_BASE && a < AOT_IMPORT_THUNK_BASE + 16u * aot_import_count) {
        if ((a - AOT_IMPORT_THUNK_BASE) & 15u) {
            fprintf(stderr, "[ref] misaligned import thunk %08x\n", a);
            exit(4);
        }
        aot_import_table[(a - AOT_IMPORT_THUNK_BASE) / 16u].fn(c);
    } else if (a >= AOT_JNI_THUNK_BASE && a < AOT_JNI_THUNK_BASE + 4u * AOT_JNI_THUNK_COUNT) {
        aot_jni_dispatch(c, (a - AOT_JNI_THUNK_BASE) / 4u);
    } else {
        fprintf(stderr, "[ref] execution reached unmapped thunk page at %08x\n", a);
        exit(4);
    }
    sync_out(c);
}

static void map_ptr(uint32_t lo, uint64_t hi)
{
    uint64_t a = lo & ~0xFFFull, b = (hi + 0xFFFull) & ~0xFFFull;
    check(uc_mem_map_ptr(g_uc, a, (size_t)(b - a), UC_PROT_ALL, aot_mem + a), "mem_map_ptr");
}

static void setup(void)
{
    static uint32_t bx_lr[0x20000 / 4];
    uc_hook h;
    uint32_t v;
    check(uc_open(UC_ARCH_ARM, UC_MODE_ARM, &g_uc), "open");
    check(uc_ctl_set_cpu_model(g_uc, UC_CPU_ARM_CORTEX_A8), "cpu model");
    /* enable VFP: CPACR full access to cp10/cp11, FPEXC.EN */
    v = 0x00F00000u;
    check(uc_reg_write(g_uc, UC_ARM_REG_C1_C0_2, &v), "cpacr");
    v = 0x40000000u;
    check(uc_reg_write(g_uc, UC_ARM_REG_FPEXC, &v), "fpexc");
    v = 0; /* FPSCR = 0: round-to-nearest, no flush-to-zero, no default-NaN */
    check(uc_reg_write(g_uc, UC_ARM_REG_FPSCR, &v), "fpscr");
    map_ptr(AOT_B, aot_image_end);
    map_ptr(AOT_RTDATA_BASE, AOT_RTDATA_BASE + AOT_RTDATA_SIZE);
    map_ptr(AOT_HEAP_BASE, AOT_HEAP_BASE + 0x40000000ull);      /* 1 GiB of heap window */
    map_ptr(AOT_STACK_BASE, AOT_STACK_BASE + 16u * AOT_STACK_SIZE);
    for (size_t i = 0; i < sizeof bx_lr / 4; ++i) bx_lr[i] = 0xE12FFF1Eu;
    check(uc_mem_map(g_uc, AOT_IMPORT_THUNK_BASE, 0x20000, UC_PROT_ALL), "map thunks");
    check(uc_mem_write(g_uc, AOT_IMPORT_THUNK_BASE, bx_lr, sizeof bx_lr), "write thunks");
    check(uc_mem_map(g_uc, 0xFFFFF000u, 0x1000, UC_PROT_ALL), "map magic");
    check(uc_mem_write(g_uc, 0xFFFFF000u, bx_lr, 0x1000), "write magic");
    check(uc_hook_add(g_uc, &h, UC_HOOK_CODE, thunk_hook, NULL, AOT_IMPORT_THUNK_BASE,
                      AOT_IMPORT_THUNK_BASE + 0x20000 - 1),
          "hook");
    if (getenv("SM_REF_COUNT")) {
        check(uc_hook_add(g_uc, &h, UC_HOOK_BLOCK, block_hook, NULL, AOT_B, aot_image_end), "block hook");
    }
}

static uint32_t ref_invoke(aot_cpu *c, uint32_t addr, const uint32_t *args, int nargs, uint32_t *hi)
{
    uc_context *ctx = NULL;
    aot_cpu *prev = g_cur;
    uint32_t sp, r0, r1, spv;
    int i, nstack = nargs > 4 ? nargs - 4 : 0;
    uc_err e;
    if (!g_uc) setup();
    if (c->depth) {
        /* nested call from inside a hook (e.g. qsort -> guest comparator) */
        check(uc_context_alloc(g_uc, &ctx), "ctx alloc");
        check(uc_context_save(g_uc, ctx), "ctx save");
        sync_in(c);
    }
    sp = c->depth ? c->r[13] : c->stack_hi;
    sp = (sp - 4u * (uint32_t)nstack) & ~7u;
    for (i = 0; i < nstack; ++i) AOT_ST32(sp + 4u * (uint32_t)i, args[4 + i]);
    for (i = 0; i < 4; ++i) {
        uint32_t v = i < nargs ? args[i] : 0;
        uc_reg_write(g_uc, k_regs[i], &v);
    }
    uc_reg_write(g_uc, UC_ARM_REG_SP, &sp);
    {
        uint32_t lr = AOT_RETURN_MAGIC;
        uc_reg_write(g_uc, UC_ARM_REG_LR, &lr);
    }
    g_cur = c;
    c->depth++;
    g_emu_starts++;
    e = uc_emu_start(g_uc, addr, AOT_RETURN_MAGIC, 0, 0);
    c->depth--;
    if (e != UC_ERR_OK) {
        uint32_t pc;
        uc_reg_read(g_uc, UC_ARM_REG_PC, &pc);
        fprintf(stderr, "[ref] emulation error in call to %08x (v7a:%#x) at pc %08x (v7a:%#x): %s\n", addr,
                addr - AOT_B, pc, pc - AOT_B, uc_strerror(e));
        exit(4);
    }
    uc_reg_read(g_uc, UC_ARM_REG_R0, &r0);
    uc_reg_read(g_uc, UC_ARM_REG_R1, &r1);
    uc_reg_read(g_uc, UC_ARM_REG_SP, &spv);
    if (spv != sp) {
        fprintf(stderr, "[ref] call to %08x returned with sp %08x, expected %08x\n", addr, spv, sp);
        exit(4);
    }
    if (ctx) {
        check(uc_context_restore(g_uc, ctx), "ctx restore");
        uc_context_free(ctx);
    }
    g_cur = prev;
    if (hi) *hi = r1;
    return r0;
}

int main(int argc, char **argv)
{
    aot_invoke_backend = ref_invoke;
    fprintf(stderr, "[ref] REFERENCE run: original ARM32 code in Unicorn %s\n", uc_version(NULL, NULL) ? "2" : "");
    {
        int rc = sm_host_run(argc, argv);
        fprintf(stderr, "[ref] uc_emu_start calls: %llu; original-code blocks: %llu, instructions: %llu%s\n",
                (unsigned long long)g_emu_starts, (unsigned long long)g_blocks, (unsigned long long)g_insns,
                getenv("SM_REF_COUNT") ? "" : " (set SM_REF_COUNT=1 to count)");
        return rc;
    }
}
