/*
 * Guest JNIEnv. The guest calls JNI exactly as the original did
 * (ldr rX,[env]; ldr pc,[rX,#off]); the function table holds JNI thunk
 * addresses, which aot_call() routes here by slot index. Slot indices follow
 * the JNINativeInterface order (jni.h); the 14 used by this binary are those
 * in docs/JNI_MAP.json. Objects cross as 32-bit handles (aot_handle_*).
 */
#include <stdlib.h>
#include <string.h>

#include "aot_host.h"

uint32_t aot_rtdata_alloc(uint32_t n);
void *aot_thread_java_ctx(void);

static uint32_t g_table;

void aot_jni_init_tables(void)
{
    uint32_t i;
    g_table = aot_rtdata_alloc(4u * AOT_JNI_THUNK_COUNT);
    for (i = 0; i < AOT_JNI_THUNK_COUNT; ++i) {
        AOT_ST32(g_table + 4u * i, AOT_JNI_THUNK_BASE + 4u * i);
    }
}

uint32_t aot_jni_new_env(void)
{
    uint32_t e = aot_rtdata_alloc(16);
    AOT_ST32(e, g_table);
    return e;
}

/* ---------------------------------------------------------------- method/field IDs */
typedef struct {
    void *id;
    char ret;
    char args[32];
    int nargs;
} method_rec;
#define MAX_IDS 512
static method_rec g_methods[MAX_IDS];
static void *g_fields[MAX_IDS];
static int g_nmethods, g_nfields;
#define METHOD_ID_BASE 0x00060000u
#define FIELD_ID_BASE 0x00070000u

static void parse_sig(const char *sig, method_rec *m)
{
    const char *p = sig;
    m->nargs = 0;
    if (*p++ != '(') aot_fatal("bad JNI signature %s", sig);
    while (*p && *p != ')') {
        char t = *p;
        if (m->nargs >= 32) aot_fatal("too many args in %s", sig);
        if (t == '[') {
            while (*p == '[') ++p;
            if (*p == 'L') p = strchr(p, ';') + 1;
            else ++p;
            m->args[m->nargs++] = 'L';
            continue;
        }
        if (t == 'L') {
            p = strchr(p, ';') + 1;
            m->args[m->nargs++] = 'L';
            continue;
        }
        m->args[m->nargs++] = t;
        ++p;
    }
    if (*p != ')') aot_fatal("bad JNI signature %s", sig);
    m->ret = p[1] == '[' ? 'L' : p[1];
}

static inline uint32_t arg(aot_cpu *c, int i)
{
    return i < 4 ? c->r[i] : AOT_LD32(c->r[13] + 4u * (uint32_t)(i - 4));
}
static inline const char *gstr(uint32_t p) { return (const char *)aot_host(p); }

static void call_v(aot_cpu *c, char want_ret)
{
    void *ctx = aot_thread_java_ctx();
    void *obj = aot_handle_get(c->r[1]);
    uint32_t mh = c->r[2], va = c->r[3];
    method_rec *m;
    aot_jvalue args[32], r;
    int i;
    if (mh < METHOD_ID_BASE || mh >= METHOD_ID_BASE + 4u * (uint32_t)g_nmethods || (mh & 3u)) {
        aot_fatal("invalid jmethodID %08x", mh);
    }
    m = &g_methods[(mh - METHOD_ID_BASE) / 4u];
    /* ARM EABI va_list: pointer into the argument area; ints/objects 4 bytes,
     * float promoted to double, long/double 8-byte aligned. */
    for (i = 0; i < m->nargs; ++i) {
        switch (m->args[i]) {
        case 'Z': case 'B': case 'C': case 'S': case 'I':
            args[i].i = (int32_t)AOT_LD32(va);
            va += 4;
            break;
        case 'L':
            args[i].l = aot_handle_get(AOT_LD32(va));
            va += 4;
            break;
        case 'F': case 'D': {
            double d;
            va = (va + 7u) & ~7u;
            d = aot_u2d(AOT_LD32(va), AOT_LD32(va + 4));
            va += 8;
            if (m->args[i] == 'F') args[i].f = (float)d;
            else args[i].d = d;
            break;
        }
        case 'J':
            va = (va + 7u) & ~7u;
            args[i].j = (int64_t)(((uint64_t)AOT_LD32(va + 4) << 32) | AOT_LD32(va));
            va += 8;
            break;
        default:
            aot_fatal("unsupported JNI arg type %c", m->args[i]);
        }
    }
    if (m->ret != want_ret && !(want_ret == 'I' && strchr("ZBCSI", m->ret))) {
        aot_fatal("JNI Call%sMethodV on method returning %c", want_ret == 'V' ? "Void" : "Int", m->ret);
    }
    r = aot_cfg->java->call_method(ctx, obj, m->id, m->ret, args, m->nargs);
    c->r[0] = want_ret == 'V' ? 0u : (uint32_t)r.i;
}

void aot_jni_dispatch(aot_cpu *c, uint32_t index)
{
    const aot_java_ops *j = aot_cfg->java;
    void *ctx = aot_thread_java_ctx();
    if (!j) aot_fatal("JNI call %u without a Java bridge", index);
    switch (index) {
    case 6: /* FindClass(env, name) */
        c->r[0] = aot_handle_new(j->find_class(ctx, gstr(c->r[1])), 1);
        break;
    case 21: /* NewGlobalRef(env, obj) */
        c->r[0] = c->r[1] ? aot_handle_new(j->new_global_ref(ctx, aot_handle_get(c->r[1])), 1) : 0;
        break;
    case 23: /* DeleteLocalRef(env, obj) */
        aot_handle_release(ctx, c->r[1]);
        break;
    case 31: /* GetObjectClass(env, obj) */
        c->r[0] = aot_handle_new(j->get_object_class(ctx, aot_handle_get(c->r[1])), 1);
        break;
    case 33: { /* GetMethodID(env, cls, name, sig) */
        void *id = j->get_method_id(ctx, aot_handle_get(c->r[1]), gstr(c->r[2]), gstr(c->r[3]));
        int i;
        if (!id) {
            c->r[0] = 0;
            break;
        }
        for (i = 0; i < g_nmethods; ++i) {
            if (g_methods[i].id == id) break;
        }
        if (i == g_nmethods) {
            if (g_nmethods >= MAX_IDS) aot_fatal("too many method IDs");
            g_methods[i].id = id;
            parse_sig(gstr(c->r[3]), &g_methods[i]);
            ++g_nmethods;
        }
        c->r[0] = METHOD_ID_BASE + 4u * (uint32_t)i;
        break;
    }
    case 50: /* CallIntMethodV(env, obj, mid, va_list) */
        call_v(c, 'I');
        break;
    case 62: /* CallVoidMethodV(env, obj, mid, va_list) */
        call_v(c, 'V');
        break;
    case 94: { /* GetFieldID(env, cls, name, sig) */
        void *id = j->get_field_id(ctx, aot_handle_get(c->r[1]), gstr(c->r[2]), gstr(c->r[3]));
        int i;
        if (!id) {
            c->r[0] = 0;
            break;
        }
        for (i = 0; i < g_nfields; ++i) {
            if (g_fields[i] == id) break;
        }
        if (i == g_nfields) {
            if (g_nfields >= MAX_IDS) aot_fatal("too many field IDs");
            g_fields[g_nfields++] = id;
        }
        c->r[0] = FIELD_ID_BASE + 4u * (uint32_t)i;
        break;
    }
    case 100: { /* GetIntField(env, obj, fid) */
        uint32_t fh = c->r[2];
        if (fh < FIELD_ID_BASE || fh >= FIELD_ID_BASE + 4u * (uint32_t)g_nfields) aot_fatal("bad jfieldID %08x", fh);
        c->r[0] = (uint32_t)j->get_int_field(ctx, aot_handle_get(c->r[1]), g_fields[(fh - FIELD_ID_BASE) / 4u]);
        break;
    }
    case 167: /* NewStringUTF(env, chars) */
        c->r[0] = aot_handle_new(j->new_string_utf(ctx, gstr(c->r[1])), 1);
        break;
    case 169: { /* GetStringUTFChars(env, str, isCopy) */
        char *h = j->get_string_utf_chars(ctx, aot_handle_get(c->r[1]));
        size_t n = h ? strlen(h) : 0;
        uint32_t g = aot_malloc((uint32_t)n + 1u);
        if (!g) aot_fatal("out of guest memory");
        memcpy(aot_host(g), h ? h : "", n + 1);
        free(h);
        if (c->r[2]) AOT_ST8(c->r[2], 1);
        c->r[0] = g;
        break;
    }
    case 170: /* ReleaseStringUTFChars(env, str, chars) */
        aot_free(c->r[2]);
        break;
    case 176: /* NewByteArray(env, len) */
        c->r[0] = aot_handle_new(j->new_byte_array(ctx, (int32_t)c->r[1]), 1);
        break;
    case 200: /* GetByteArrayRegion(env, arr, start, len, buf) */
        j->get_byte_array_region(ctx, aot_handle_get(c->r[1]), (int32_t)c->r[2], (int32_t)c->r[3],
                                 aot_host(arg(c, 4)));
        break;
    case 208: /* SetByteArrayRegion(env, arr, start, len, buf) */
        j->set_byte_array_region(ctx, aot_handle_get(c->r[1]), (int32_t)c->r[2], (int32_t)c->r[3],
                                 aot_host(arg(c, 4)));
        break;
    default:
        aot_fatal("guest used JNI function slot %u (offset %#x), not provided", index, index * 4u);
    }
}
