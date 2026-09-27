/*
 * Headless GL backend: accepts every call, allocates object names, draws
 * nothing. Used where no GL library exists (e.g. AArch64 runs under
 * qemu-user) together with the runtime's GL call trace.
 */
#include <string.h>

#include "sm_gl_api.h"

static sm_GLuint g_next_tex = 1, g_next_buf = 1;

static void null_GenTextures(sm_GLsizei n, sm_GLuint *t)
{
    for (sm_GLsizei i = 0; i < n; ++i) t[i] = g_next_tex++;
}
static void null_GenBuffers(sm_GLsizei n, sm_GLuint *b)
{
    for (sm_GLsizei i = 0; i < n; ++i) b[i] = g_next_buf++;
}
static void null_ReadPixels(sm_GLint x, sm_GLint y, sm_GLsizei w, sm_GLsizei h, sm_GLenum f, sm_GLenum t, void *p)
{
    (void)x;
    (void)y;
    (void)t;
    memset(p, 0, (size_t)w * (size_t)h * (f == 0x1908u ? 4u : 3u));
}

/* all entry points default to "no effect"; the three above are overridden */
#define DEF_NOOP(name, ret, params, args) \
    static ret null_noop_##name params { SM_UNUSED_ARGS args }
#define SM_UNUSED_ARGS(...) sm_unused(0, ##__VA_ARGS__);
static inline void sm_unused(int dummy, ...) { (void)dummy; }
SM_GL_FUNCS(DEF_NOOP)

static const sm_gl_backend g_null = {
#define PICK(name, ret, params, args) .name = null_noop_##name,
    SM_GL_FUNCS(PICK)
#undef PICK
};

const sm_gl_backend *sm_null_gl_backend(void)
{
    static sm_gl_backend b;
    b = g_null;
    b.GenTextures = null_GenTextures;
    b.GenBuffers = null_GenBuffers;
    b.ReadPixels = null_ReadPixels;
    return &b;
}
