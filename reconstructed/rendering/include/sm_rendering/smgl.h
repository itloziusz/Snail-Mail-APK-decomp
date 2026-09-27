/*
 * sm_rendering: GLES 1.1 fixed-function emulation on top of GLES 2.0 for the
 * 45 entry points the original libsnailmail.so imports (aot/runtime/sm_gl_api.h,
 * analysis/native/gl_usage.json). Design and every semantic choice:
 * docs/RENDERING.md.
 *
 * Usage: make a GLES 2.0 (or later ES) context current, call smgl_init(),
 * then drive the smgl_<Name> functions (or the table from smgl_backend())
 * exactly as a GLES 1.1 application would call gl<Name>. The emulator owns the
 * GLES2 program, vertex attributes 0/1 and texture unit 0 of that context.
 * Single-threaded, one context at a time (like GLES itself).
 */
#ifndef SM_RENDERING_SMGL_H
#define SM_RENDERING_SMGL_H

#include "sm_gl_api.h"

#ifdef __cplusplus
extern "C" {
#endif

/* One function per SM_GL_FUNCS entry, with exactly its signature. */
#define SMGL_DECLARE_ENTRY(name, ret, params, args) ret smgl_##name params;
SM_GL_FUNCS(SMGL_DECLARE_ENTRY)
#undef SMGL_DECLARE_ENTRY

/* Table of all smgl_<Name> functions (static storage, never NULL). */
const sm_gl_backend *smgl_backend(void);

/* Compile/link the shader and reset all emulated state to the GLES 1.1
 * defaults. Requires a current GLES2+ context. Returns 0 on success, -1 on
 * failure (diagnostic on stderr). Calling it again re-initialises. */
int smgl_init(void);

/* Delete the GL objects created by smgl_init (context must still be current)
 * and free the emulator's bookkeeping. */
void smgl_shutdown(void);

/* ---- Introspection (not part of the GLES 1.1 import surface) ---- */

/* glGetError semantics for errors the emulator itself detects (GLES 1.1
 * validation, stack overflow/underflow): returns the first recorded error
 * since the last call and clears it; 0 (GL_NO_ERROR) if none. */
sm_GLenum smgl_take_error(void);

/* Current top of the MODELVIEW / PROJECTION / TEXTURE stack (column-major).
 * Returns 0, or -1 for an unknown mode. */
int smgl_get_matrix(sm_GLenum mode, sm_GLfloat out[16]);

/* Current depth (>= 1) of the named stack, or -1 for an unknown mode. */
int smgl_get_stack_depth(sm_GLenum mode);

/* Diagnostics: each category is logged to stderr once per entry point and
 * counted every time it occurs. */
typedef enum smgl_diag {
    SMGL_DIAG_NOT_INITIALIZED = 0,  /* entry point called before smgl_init */
    SMGL_DIAG_GL_ERROR,             /* GLES 1.1 validation error recorded */
    SMGL_DIAG_STACK_OVERFLOW,       /* glPushMatrix on a full stack */
    SMGL_DIAG_STACK_UNDERFLOW,      /* glPopMatrix on a depth-1 stack */
    SMGL_DIAG_STACK_BEYOND_MINIMUM, /* depth above the GLES 1.1 guaranteed minimum */
    SMGL_DIAG_COLOR_ARRAY_DRAW,     /* draw with GL_COLOR_ARRAY enabled (ignored) */
    SMGL_DIAG_NORMAL_ARRAY_DRAW,    /* draw with GL_NORMAL_ARRAY enabled (ignored) */
    SMGL_DIAG_POINT_SIZE_ARRAY_DRAW,/* draw with GL_POINT_SIZE_ARRAY_OES enabled (ignored) */
    SMGL_DIAG_ALPHA_TEST_DRAW,      /* draw with GL_ALPHA_TEST enabled (func is ALWAYS) */
    SMGL_DIAG_UNSUPPORTED_CAP,      /* glEnable of lighting/clip planes/etc. (ignored) */
    SMGL_DIAG_UNSUPPORTED_TEXENV,   /* texenv pname/mode not emulated */
    SMGL_DIAG_UNSUPPORTED_PARAM,    /* other state the emulator does not implement */
    SMGL_DIAG_TEX_INCOMPLETE,       /* texturing enabled with incomplete texture */
    SMGL_DIAG_TEX_NPOT,             /* non-power-of-two texture image */
    SMGL_DIAG_GENERATE_MIPMAP,      /* GL_GENERATE_MIPMAP emulated with glGenerateMipmap */
    SMGL_DIAG_READPIXELS_CONVERTED, /* GL_RGB readback converted from RGBA */
    SMGL_DIAG_DRAW_WITHOUT_VERTEX_ARRAY, /* draw with GL_VERTEX_ARRAY disabled (no-op) */
    SMGL_DIAG_NULL_POINTER,         /* NULL where GLES would dereference (ignored call) */
    SMGL_DIAG_BACKEND_GL_ERROR,     /* SMGL_DEBUG=1: GLES2 reported an error */
    SMGL_DIAG__COUNT
} smgl_diag;

unsigned long smgl_diag_count(smgl_diag d);
const char *smgl_diag_name(smgl_diag d);

/* Matrix-stack depths of this implementation and the GLES 1.1 minima. */
#define SMGL_MODELVIEW_STACK_DEPTH 32
#define SMGL_PROJECTION_STACK_DEPTH 4
#define SMGL_TEXTURE_STACK_DEPTH 4
#define SMGL_ES11_MIN_MODELVIEW_STACK_DEPTH 16
#define SMGL_ES11_MIN_PROJECTION_STACK_DEPTH 2
#define SMGL_ES11_MIN_TEXTURE_STACK_DEPTH 2

/* GLES 1.1 tokens that <GLES2/gl2.h> does not define (values from the
 * Khronos GLES 1.1 <GLES/gl.h>). */
#define SMGL_GL_STACK_OVERFLOW 0x0503
#define SMGL_GL_STACK_UNDERFLOW 0x0504
#define SMGL_GL_EXP 0x0800
#define SMGL_GL_EXP2 0x0801
#define SMGL_GL_ADD 0x0104
#define SMGL_GL_POINT_SMOOTH 0x0B10
#define SMGL_GL_LINE_SMOOTH 0x0B20
#define SMGL_GL_LIGHTING 0x0B50
#define SMGL_GL_COLOR_MATERIAL 0x0B57
#define SMGL_GL_FOG 0x0B60
#define SMGL_GL_FOG_DENSITY 0x0B62
#define SMGL_GL_FOG_START 0x0B63
#define SMGL_GL_FOG_END 0x0B64
#define SMGL_GL_FOG_MODE 0x0B65
#define SMGL_GL_FOG_COLOR 0x0B66
#define SMGL_GL_NORMALIZE 0x0BA1
#define SMGL_GL_ALPHA_TEST 0x0BC0
#define SMGL_GL_COLOR_LOGIC_OP 0x0BF2
#define SMGL_GL_PERSPECTIVE_CORRECTION_HINT 0x0C50
#define SMGL_GL_POINT_SMOOTH_HINT 0x0C51
#define SMGL_GL_LINE_SMOOTH_HINT 0x0C52
#define SMGL_GL_FOG_HINT 0x0C54
#define SMGL_GL_FLAT 0x1D00
#define SMGL_GL_SMOOTH 0x1D01
#define SMGL_GL_MODELVIEW 0x1700
#define SMGL_GL_PROJECTION 0x1701
#define SMGL_GL_TEXTURE 0x1702
#define SMGL_GL_MODULATE 0x2100
#define SMGL_GL_DECAL 0x2101
#define SMGL_GL_TEXTURE_ENV_MODE 0x2200
#define SMGL_GL_TEXTURE_ENV_COLOR 0x2201
#define SMGL_GL_TEXTURE_ENV 0x2300
#define SMGL_GL_CLIP_PLANE0 0x3000
#define SMGL_GL_LIGHT0 0x4000
#define SMGL_GL_RESCALE_NORMAL 0x803A
#define SMGL_GL_VERTEX_ARRAY 0x8074
#define SMGL_GL_NORMAL_ARRAY 0x8075
#define SMGL_GL_COLOR_ARRAY 0x8076
#define SMGL_GL_TEXTURE_COORD_ARRAY 0x8078
#define SMGL_GL_MULTISAMPLE 0x809D
#define SMGL_GL_SAMPLE_ALPHA_TO_ONE 0x809F
#define SMGL_GL_GENERATE_MIPMAP 0x8191
#define SMGL_GL_COMBINE 0x8570
#define SMGL_GL_POINT_SPRITE_OES 0x8861
#define SMGL_GL_POINT_SIZE_ARRAY_OES 0x8B9C

#ifdef __cplusplus
}
#endif
#endif /* SM_RENDERING_SMGL_H */
