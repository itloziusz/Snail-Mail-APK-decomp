/*
 * Host-facing API of the AOT runtime: what an embedder (Android JNI bridge,
 * Linux host runner, test harness) uses to start the translated game and to
 * provide the platform services it imports.
 */
#ifndef AOT_HOST_H
#define AOT_HOST_H

#include <stdint.h>
#include <stdio.h>

#include "aot_rt.h"
#include "sm_gl_api.h"

#ifdef __cplusplus
extern "C" {
#endif

/* ---------------------------------------------------------------- guest layout
 * 0x00000000-0x000FFFFF  never mapped (NULL guard). Import thunks (0x10000),
 *                        JNI thunks (0x20000) and object handles (0xC0000)
 *                        are values in this range: jumping to or reading them
 *                        faults or is intercepted, never executed.
 * 0x00080000-0x000BFFFF  runtime data (guest JNIEnv, JNI table, __sF, ...)
 * 0x00100000-...         original image (AOT_B + vaddr)
 * 0x10000000-0xDFFFFFFF  guest heap (malloc / operator new)
 * 0xE0000000-0xEFFFFFFF  guest stacks, 2 MiB per host thread
 */
#define AOT_RTDATA_BASE 0x00080000u
#define AOT_RTDATA_SIZE 0x00040000u
#define AOT_HANDLE_BASE 0x000C0000u
#define AOT_HANDLE_MAX 0x00010000u
#define AOT_HEAP_BASE 0x10000000u
#define AOT_HEAP_END 0xE0000000u
#define AOT_STACK_BASE 0xE0000000u
#define AOT_STACK_SIZE 0x00200000u
#define AOT_RETURN_MAGIC 0xFFFFFFF0u

/* ---------------------------------------------------------------- Java bridge
 * Objects, classes, method and field IDs are opaque host pointers. The runtime
 * maps them to 32-bit guest handles; the guest never sees a host pointer.
 * `ctx` is per-thread (the JNIEnv* on Android). */
typedef union aot_jvalue {
    int32_t i;
    int64_t j;
    float f;
    double d;
    void *l;
} aot_jvalue;

typedef struct aot_java_ops {
    void *(*find_class)(void *ctx, const char *name);
    void *(*get_object_class)(void *ctx, void *obj);
    void *(*new_global_ref)(void *ctx, void *obj);
    void (*delete_ref)(void *ctx, void *obj);
    int (*is_same_object)(void *ctx, void *a, void *b);
    void *(*get_method_id)(void *ctx, void *cls, const char *name, const char *sig);
    void *(*get_field_id)(void *ctx, void *cls, const char *name, const char *sig);
    int32_t (*get_int_field)(void *ctx, void *obj, void *fid);
    /* args already decoded according to the method signature */
    aot_jvalue (*call_method)(void *ctx, void *obj, void *mid, char ret, const aot_jvalue *args, int nargs);
    void *(*new_string_utf)(void *ctx, const char *s);
    char *(*get_string_utf_chars)(void *ctx, void *str);  /* malloc'd host copy */
    void *(*new_byte_array)(void *ctx, int32_t len);
    void (*get_byte_array_region)(void *ctx, void *arr, int32_t start, int32_t len, void *buf);
    void (*set_byte_array_region)(void *ctx, void *arr, int32_t start, int32_t len, const void *buf);
} aot_java_ops;

/* ---------------------------------------------------------------- setup */
typedef struct aot_config {
    const aot_java_ops *java;
    const sm_gl_backend *gl;
    const char *files_dir;       /* sandbox for fopen()/getcwd() (dead code in this game) */
    void (*log)(int level, const char *msg);  /* 3=debug 4=info 5=warn 6=error */
    FILE *trace;                 /* optional: function/GL trace output */
} aot_config;

/* One-time: reserve guest memory, load the image, relocate, run INIT_ARRAY. */
int aot_init(const aot_config *cfg);
int aot_initialized(void);

/* Per host thread: CPU state + guest stack + guest JNIEnv. `java_ctx` is
 * stored for the JNI thunks executed on this thread. */
aot_cpu *aot_thread_enter(void *java_ctx);
uint32_t aot_thread_guest_env(void);

/* Call guest function `addr` with up to 8 32-bit arguments (AAPCS: r0-r3,
 * then the stack). Returns r0 (r1 in *hi if non-NULL). Re-entrant: state of
 * an enclosing guest call is preserved. */
uint32_t aot_invoke(aot_cpu *c, uint32_t addr, const uint32_t *args, int nargs, uint32_t *hi);
uint32_t aot_symbol_addr(const char *name);   /* 0 if unknown */

/* object handles */
uint32_t aot_handle_new(void *obj, int owned_global);
uint32_t aot_handle_for_incoming(void *java_ctx, void *obj);  /* dedupes by is_same_object */
void *aot_handle_get(uint32_t h);
void aot_handle_release(void *java_ctx, uint32_t h);

/* guest heap */
uint32_t aot_malloc(uint32_t size);
void aot_free(uint32_t p);

/* diagnostics */
void aot_log(int level, const char *fmt, ...) __attribute__((format(printf, 2, 3)));
extern const aot_config *aot_cfg;
extern uint64_t aot_calls_executed;

/* internal hooks shared between runtime files */
void aot_jni_dispatch(aot_cpu *c, uint32_t index);
void aot_jni_init_tables(void);
void aot_libc_init(void);
uint32_t aot_import_data_addr(const char *name);   /* __sF, __stack_chk_guard */
void aot_gl_trace_set(FILE *f);

#ifdef __cplusplus
}
#endif
#endif /* AOT_HOST_H */
