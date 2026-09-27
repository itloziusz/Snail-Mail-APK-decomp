/*
 * GLES 1.x imports of the original binary, marshalled from guest registers /
 * stack (softfp: floats in core registers) to an sm_gl_backend. Pointer
 * arguments that GLES interprets as buffer offsets while a buffer object is
 * bound are passed through as offsets; otherwise they become host pointers
 * into guest memory. Optional call trace for differential comparison.
 */
#include <stdio.h>

#include "aot_host.h"

#define GL_ARRAY_BUFFER_ 0x8892u
#define GL_ELEMENT_ARRAY_BUFFER_ 0x8893u

static uint32_t g_array_buffer, g_element_buffer;
static FILE *g_trace;
static uint64_t g_gl_calls;

void aot_gl_trace_set(FILE *f) { g_trace = f; }

static inline uint32_t arg(aot_cpu *c, int i)
{
    return i < 4 ? c->r[i] : AOT_LD32(c->r[13] + 4u * (uint32_t)(i - 4));
}
static inline float argf(aot_cpu *c, int i) { return aot_u2f(arg(c, i)); }
static inline const void *gptr(uint32_t p) { return p ? (const void *)aot_host(p) : NULL; }

static uint32_t fnv(const void *p, size_t n)
{
    const uint8_t *b = (const uint8_t *)p;
    uint32_t h = 2166136261u;
    while (n--) {
        h ^= *b++;
        h *= 16777619u;
    }
    return h;
}

#define GLB (aot_cfg->gl)
#define IMP(name) void aot_imp_##name(aot_cpu *c)
#define T(...) do { ++g_gl_calls; if (g_trace) fprintf(g_trace, __VA_ARGS__); } while (0)

IMP(glBindBuffer)
{
    uint32_t t = arg(c, 0), b = arg(c, 1);
    if (t == GL_ARRAY_BUFFER_) g_array_buffer = b;
    if (t == GL_ELEMENT_ARRAY_BUFFER_) g_element_buffer = b;
    T("glBindBuffer %x %u\n", t, b);
    GLB->BindBuffer(t, b);
}
IMP(glBindTexture) { T("glBindTexture %x %u\n", arg(c, 0), arg(c, 1)); GLB->BindTexture(arg(c, 0), arg(c, 1)); }
IMP(glBlendFunc) { T("glBlendFunc %x %x\n", arg(c, 0), arg(c, 1)); GLB->BlendFunc(arg(c, 0), arg(c, 1)); }
IMP(glBufferData)
{
    uint32_t size = arg(c, 1), data = arg(c, 2);
    T("glBufferData %x %u %08x %x\n", arg(c, 0), size, data ? fnv(aot_host(data), size) : 0u, arg(c, 3));
    GLB->BufferData(arg(c, 0), (sm_GLsizeiptr)size, gptr(data), arg(c, 3));
}
IMP(glClear) { T("glClear %x\n", arg(c, 0)); GLB->Clear(arg(c, 0)); }
IMP(glClearColor)
{
    T("glClearColor %08x %08x %08x %08x\n", arg(c, 0), arg(c, 1), arg(c, 2), arg(c, 3));
    GLB->ClearColor(argf(c, 0), argf(c, 1), argf(c, 2), argf(c, 3));
}
IMP(glClearDepthf) { T("glClearDepthf %08x\n", arg(c, 0)); GLB->ClearDepthf(argf(c, 0)); }
IMP(glColor4f)
{
    T("glColor4f %08x %08x %08x %08x\n", arg(c, 0), arg(c, 1), arg(c, 2), arg(c, 3));
    GLB->Color4f(argf(c, 0), argf(c, 1), argf(c, 2), argf(c, 3));
}
IMP(glCullFace) { T("glCullFace %x\n", arg(c, 0)); GLB->CullFace(arg(c, 0)); }
IMP(glDeleteTextures)
{
    T("glDeleteTextures %d\n", (int)arg(c, 0));
    GLB->DeleteTextures((sm_GLsizei)arg(c, 0), (const sm_GLuint *)gptr(arg(c, 1)));
}
IMP(glDepthFunc) { T("glDepthFunc %x\n", arg(c, 0)); GLB->DepthFunc(arg(c, 0)); }
IMP(glDepthMask) { T("glDepthMask %u\n", arg(c, 0) & 0xFFu); GLB->DepthMask((sm_GLboolean)(arg(c, 0) & 0xFFu)); }
IMP(glDepthRangef)
{
    T("glDepthRangef %08x %08x\n", arg(c, 0), arg(c, 1));
    GLB->DepthRangef(argf(c, 0), argf(c, 1));
}
IMP(glDisable) { T("glDisable %x\n", arg(c, 0)); GLB->Disable(arg(c, 0)); }
IMP(glDisableClientState) { T("glDisableClientState %x\n", arg(c, 0)); GLB->DisableClientState(arg(c, 0)); }
IMP(glDrawElements)
{
    uint32_t idx = arg(c, 3);
    const void *p = g_element_buffer ? (const void *)(uintptr_t)idx : gptr(idx);
    T("glDrawElements %x %d %x %08x eb=%u\n", arg(c, 0), (int)arg(c, 1), arg(c, 2), idx, g_element_buffer);
    GLB->DrawElements(arg(c, 0), (sm_GLsizei)arg(c, 1), arg(c, 2), p);
}
IMP(glEnable) { T("glEnable %x\n", arg(c, 0)); GLB->Enable(arg(c, 0)); }
IMP(glEnableClientState) { T("glEnableClientState %x\n", arg(c, 0)); GLB->EnableClientState(arg(c, 0)); }
IMP(glFinish) { (void)c; T("glFinish\n"); GLB->Finish(); }
IMP(glFogf) { T("glFogf %x %08x\n", arg(c, 0), arg(c, 1)); GLB->Fogf(arg(c, 0), argf(c, 1)); }
IMP(glFogfv)
{
    const float *v = (const float *)gptr(arg(c, 1));
    T("glFogfv %x %08x\n", arg(c, 0), v ? fnv(v, 16) : 0u);
    GLB->Fogfv(arg(c, 0), v);
}
IMP(glFrustumf)
{
    T("glFrustumf %08x %08x %08x %08x %08x %08x\n", arg(c, 0), arg(c, 1), arg(c, 2), arg(c, 3), arg(c, 4),
      arg(c, 5));
    GLB->Frustumf(argf(c, 0), argf(c, 1), argf(c, 2), argf(c, 3), argf(c, 4), argf(c, 5));
}
IMP(glGenBuffers)
{
    GLB->GenBuffers((sm_GLsizei)arg(c, 0), (sm_GLuint *)aot_host(arg(c, 1)));
    T("glGenBuffers %d -> %u\n", (int)arg(c, 0), AOT_LD32(arg(c, 1)));
}
IMP(glGenTextures)
{
    GLB->GenTextures((sm_GLsizei)arg(c, 0), (sm_GLuint *)aot_host(arg(c, 1)));
    T("glGenTextures %d -> %u\n", (int)arg(c, 0), AOT_LD32(arg(c, 1)));
}
IMP(glHint) { T("glHint %x %x\n", arg(c, 0), arg(c, 1)); GLB->Hint(arg(c, 0), arg(c, 1)); }
IMP(glLineWidth) { T("glLineWidth %08x\n", arg(c, 0)); GLB->LineWidth(argf(c, 0)); }
IMP(glLoadIdentity) { (void)c; T("glLoadIdentity\n"); GLB->LoadIdentity(); }
IMP(glMatrixMode) { T("glMatrixMode %x\n", arg(c, 0)); GLB->MatrixMode(arg(c, 0)); }
IMP(glMultMatrixf)
{
    const float *m = (const float *)gptr(arg(c, 0));
    T("glMultMatrixf %08x\n", m ? fnv(m, 64) : 0u);
    GLB->MultMatrixf(m);
}
IMP(glOrthof)
{
    T("glOrthof %08x %08x %08x %08x %08x %08x\n", arg(c, 0), arg(c, 1), arg(c, 2), arg(c, 3), arg(c, 4),
      arg(c, 5));
    GLB->Orthof(argf(c, 0), argf(c, 1), argf(c, 2), argf(c, 3), argf(c, 4), argf(c, 5));
}
IMP(glPixelStorei) { T("glPixelStorei %x %d\n", arg(c, 0), (int)arg(c, 1)); GLB->PixelStorei(arg(c, 0), (sm_GLint)arg(c, 1)); }
IMP(glPopMatrix) { (void)c; T("glPopMatrix\n"); GLB->PopMatrix(); }
IMP(glPushMatrix) { (void)c; T("glPushMatrix\n"); GLB->PushMatrix(); }
IMP(glReadPixels)
{
    T("glReadPixels %d %d %d %d %x %x\n", (int)arg(c, 0), (int)arg(c, 1), (int)arg(c, 2), (int)arg(c, 3),
      arg(c, 4), arg(c, 5));
    GLB->ReadPixels((sm_GLint)arg(c, 0), (sm_GLint)arg(c, 1), (sm_GLsizei)arg(c, 2), (sm_GLsizei)arg(c, 3),
                    arg(c, 4), arg(c, 5), aot_host(arg(c, 6)));
}
IMP(glRotatef)
{
    T("glRotatef %08x %08x %08x %08x\n", arg(c, 0), arg(c, 1), arg(c, 2), arg(c, 3));
    GLB->Rotatef(argf(c, 0), argf(c, 1), argf(c, 2), argf(c, 3));
}
IMP(glScalef)
{
    T("glScalef %08x %08x %08x\n", arg(c, 0), arg(c, 1), arg(c, 2));
    GLB->Scalef(argf(c, 0), argf(c, 1), argf(c, 2));
}
IMP(glScissor)
{
    T("glScissor %d %d %d %d\n", (int)arg(c, 0), (int)arg(c, 1), (int)arg(c, 2), (int)arg(c, 3));
    GLB->Scissor((sm_GLint)arg(c, 0), (sm_GLint)arg(c, 1), (sm_GLsizei)arg(c, 2), (sm_GLsizei)arg(c, 3));
}
IMP(glShadeModel) { T("glShadeModel %x\n", arg(c, 0)); GLB->ShadeModel(arg(c, 0)); }
IMP(glTexCoordPointer)
{
    uint32_t p = arg(c, 3);
    T("glTexCoordPointer %d %x %d %08x ab=%u\n", (int)arg(c, 0), arg(c, 1), (int)arg(c, 2), p, g_array_buffer);
    GLB->TexCoordPointer((sm_GLint)arg(c, 0), arg(c, 1), (sm_GLsizei)arg(c, 2),
                         g_array_buffer ? (const void *)(uintptr_t)p : gptr(p));
}
IMP(glTexEnvf)
{
    T("glTexEnvf %x %x %08x\n", arg(c, 0), arg(c, 1), arg(c, 2));
    GLB->TexEnvf(arg(c, 0), arg(c, 1), argf(c, 2));
}
IMP(glTexImage2D)
{
    uint32_t w = arg(c, 3), h = arg(c, 4), fmt = arg(c, 6), type = arg(c, 7), px = arg(c, 8);
    size_t bpp = fmt == 0x1907u ? 3 : (fmt == 0x1908u ? 4 : 1);
    T("glTexImage2D %x %d %x %u %u %d %x %x %08x\n", arg(c, 0), (int)arg(c, 1), arg(c, 2), w, h, (int)arg(c, 5),
      fmt, type, px ? fnv(aot_host(px), (size_t)w * h * bpp) : 0u);
    GLB->TexImage2D(arg(c, 0), (sm_GLint)arg(c, 1), (sm_GLint)arg(c, 2), (sm_GLsizei)w, (sm_GLsizei)h,
                    (sm_GLint)arg(c, 5), fmt, type, gptr(px));
}
IMP(glTexParameteri)
{
    T("glTexParameteri %x %x %d\n", arg(c, 0), arg(c, 1), (int)arg(c, 2));
    GLB->TexParameteri(arg(c, 0), arg(c, 1), (sm_GLint)arg(c, 2));
}
IMP(glTranslatef)
{
    T("glTranslatef %08x %08x %08x\n", arg(c, 0), arg(c, 1), arg(c, 2));
    GLB->Translatef(argf(c, 0), argf(c, 1), argf(c, 2));
}
IMP(glVertexPointer)
{
    uint32_t p = arg(c, 3);
    T("glVertexPointer %d %x %d %08x ab=%u\n", (int)arg(c, 0), arg(c, 1), (int)arg(c, 2), p, g_array_buffer);
    GLB->VertexPointer((sm_GLint)arg(c, 0), arg(c, 1), (sm_GLsizei)arg(c, 2),
                       g_array_buffer ? (const void *)(uintptr_t)p : gptr(p));
}
IMP(glViewport)
{
    T("glViewport %d %d %d %d\n", (int)arg(c, 0), (int)arg(c, 1), (int)arg(c, 2), (int)arg(c, 3));
    GLB->Viewport((sm_GLint)arg(c, 0), (sm_GLint)arg(c, 1), (sm_GLsizei)arg(c, 2), (sm_GLsizei)arg(c, 3));
}

uint64_t aot_gl_call_count(void) { return g_gl_calls; }
