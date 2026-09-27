/*
 * GLES 1.1 fixed-function emulation on GLES 2.0 for the 45 entry points the
 * original libsnailmail.so imports (v7a sha256 e43bc913...a466;
 * aot/runtime/sm_gl_api.h, analysis/native/gl_usage.json).
 *
 * Every semantic choice (fog distance, texture completeness, stack depths,
 * tracked-only state, ...) is documented in docs/RENDERING.md. In short:
 *  - matrices, current colour, texenv, fog, client-array enables, alpha test,
 *    shade model, hints and GL_MULTISAMPLE are emulator state;
 *  - one GLSL ES 1.00 program implements transform, texture matrix,
 *    texenv (MODULATE/REPLACE/DECAL/BLEND/ADD) and fog (LINEAR/EXP/EXP2);
 *  - buffers, textures, blending, depth, culling, scissor, viewport and
 *    clears are forwarded to GLES2, after GLES 1.1 validation where GLES2
 *    accepts more than GLES 1.1 does;
 *  - errors the emulator detects are recorded with glGetError semantics
 *    (smgl_take_error) and logged once per entry point; unsupported state is
 *    never silently accepted.
 */
#include "sm_rendering/smgl.h"
#include "sm_rendering/smgl_math.h"

#include <GLES2/gl2.h>

#include <math.h>
#include <stdarg.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

_Static_assert(sizeof(sm_GLenum) == sizeof(GLenum), "GLenum size");
_Static_assert(sizeof(sm_GLuint) == sizeof(GLuint), "GLuint size");
_Static_assert(sizeof(sm_GLint) == sizeof(GLint), "GLint size");
_Static_assert(sizeof(sm_GLsizei) == sizeof(GLsizei), "GLsizei size");
_Static_assert(sizeof(sm_GLbitfield) == sizeof(GLbitfield), "GLbitfield size");
_Static_assert(sizeof(sm_GLboolean) == sizeof(GLboolean), "GLboolean size");
_Static_assert(sizeof(sm_GLfloat) == sizeof(GLfloat), "GLfloat size");
_Static_assert(sizeof(sm_GLsizeiptr) == sizeof(GLsizeiptr), "GLsizeiptr size");

#if defined(__GNUC__) || defined(__clang__)
#define SMGL_PRINTF(fmt_idx, first_arg) __attribute__((format(printf, fmt_idx, first_arg)))
#else
#define SMGL_PRINTF(fmt_idx, first_arg)
#endif

/* GLES 1.1 texenv combiner tokens (only used to diagnose them). */
#define SMGL_GL_COMBINE_RGB 0x8571
#define SMGL_GL_COMBINE_ALPHA 0x8572
#define SMGL_GL_RGB_SCALE 0x8573
#define SMGL_GL_ALPHA_SCALE 0x0D1C
#define SMGL_GL_SRC0_RGB 0x8580
#define SMGL_GL_OPERAND2_ALPHA 0x859A

/* ------------------------------------------------------------------------ */
/* Entry-point indices (for once-per-entry diagnostics).                      */

enum {
#define SMGL_FN_ENUM(name, ret, params, args) SMGL_FN_##name,
    SM_GL_FUNCS(SMGL_FN_ENUM)
#undef SMGL_FN_ENUM
    SMGL_FN__COUNT,
    SMGL_FN_INTERNAL = SMGL_FN__COUNT,
    SMGL_FN__SLOTS
};
_Static_assert(SMGL_FN__COUNT == 45, "sm_gl_api.h lists the 45 imported GLES 1.x functions");

static const char *const k_fn_names[SMGL_FN__SLOTS] = {
#define SMGL_FN_NAME(name, ret, params, args) "gl" #name,
    SM_GL_FUNCS(SMGL_FN_NAME)
#undef SMGL_FN_NAME
    "smgl",
};

static const char *const k_diag_names[] = {
    "not-initialized",     "gl-error",          "stack-overflow",       "stack-underflow",
    "stack-beyond-es11-minimum", "color-array-draw", "normal-array-draw",
    "point-size-array-draw", "alpha-test-draw",  "unsupported-cap",      "unsupported-texenv",
    "texture-incomplete",  "texture-npot",      "generate-mipmap",      "readpixels-converted",
    "draw-without-vertex-array", "null-pointer", "backend-gl-error",
};
_Static_assert(sizeof k_diag_names / sizeof k_diag_names[0] == SMGL_DIAG__COUNT, "diag names");

/* ------------------------------------------------------------------------ */
/* State.                                                                     */

enum { SMGL_ATTR_POSITION = 0, SMGL_ATTR_TEXCOORD = 1 };
enum { SMGL_STACK_MODELVIEW = 0, SMGL_STACK_PROJECTION = 1, SMGL_STACK_TEXTURE = 2 };
enum {
    SMGL_DIRTY_MODELVIEW = 1u << 0,
    SMGL_DIRTY_PROJECTION = 1u << 1,
    SMGL_DIRTY_TEXMATRIX = 1u << 2,
    SMGL_DIRTY_COLOR = 1u << 3,
    SMGL_DIRTY_TEXTURE = 1u << 4,
    SMGL_DIRTY_FOG = 1u << 5,
    SMGL_DIRTY_ALL = 0x3Fu
};
/* Shader texenv codes. */
enum { SMGL_TEXMODE_OFF = 0, SMGL_TEXMODE_MODULATE, SMGL_TEXMODE_REPLACE, SMGL_TEXMODE_DECAL,
       SMGL_TEXMODE_BLEND, SMGL_TEXMODE_ADD };
/* Shader fog codes. */
enum { SMGL_FOGMODE_OFF = 0, SMGL_FOGMODE_LINEAR, SMGL_FOGMODE_EXP, SMGL_FOGMODE_EXP2 };

typedef struct smgl_texrec {
    GLuint name;
    GLenum base_format; /* 0 until level 0 has been specified */
    GLsizei width, height; /* level 0 */
    uint32_t levels;    /* bit i set: level i specified */
    GLenum min_filter;
    int generate_mipmap;
} smgl_texrec;

typedef struct smgl_state {
    int initialized;
    int debug;
    GLuint program, vs, fs;
    GLint loc_modelview, loc_projection, loc_texmatrix, loc_color, loc_sampler;
    GLint loc_tex_mode, loc_tex_format, loc_fog_mode, loc_fog_params, loc_fog_color;
    GLint max_texture_size;

    GLenum error;
    unsigned dirty;

    smgl_matstack stacks[3];
    int matrix_mode; /* SMGL_STACK_* */

    float color[4]; /* current colour as specified (clamped when used) */

    int tex2d_enabled;
    GLenum env_mode;
    smgl_texrec *tex;
    size_t tex_count, tex_cap;
    GLuint bound_tex;
    /* Derived at uniform upload, reported at draw time. */
    int tex_incomplete_active, tex_combine_active, tex_decal_undefined;

    int fog_enabled;
    GLenum fog_mode;
    float fog_density, fog_start, fog_end;
    float fog_color[4];

    int alpha_test_enabled;
    int multisample_enabled;
    GLenum shade_model;
    GLenum hint_perspective, hint_point_smooth, hint_line_smooth, hint_fog;
    GLint pack_alignment;

    int va_vertex, va_texcoord, va_color, va_normal, va_point_size;
} smgl_state;

static smgl_state st;
static unsigned long g_diag_count[SMGL_DIAG__COUNT];
static unsigned char g_diag_logged[SMGL_DIAG__COUNT][SMGL_FN__SLOTS];

/* ------------------------------------------------------------------------ */
/* Diagnostics and errors.                                                    */

static void diag(smgl_diag d, int fn, const char *fmt, ...) SMGL_PRINTF(3, 4);

static void diag(smgl_diag d, int fn, const char *fmt, ...)
{
    g_diag_count[d] += 1;
    if (g_diag_logged[d][fn]) {
        return;
    }
    g_diag_logged[d][fn] = 1;
    va_list ap;
    va_start(ap, fmt);
    fprintf(stderr, "smgl: [%s] %s: ", k_diag_names[d], k_fn_names[fn]);
    vfprintf(stderr, fmt, ap);
    fprintf(stderr, " [logged once per entry point]\n");
    va_end(ap);
}

static const char *gl_error_name(GLenum e)
{
    switch (e) {
    case GL_INVALID_ENUM: return "GL_INVALID_ENUM";
    case GL_INVALID_VALUE: return "GL_INVALID_VALUE";
    case GL_INVALID_OPERATION: return "GL_INVALID_OPERATION";
    case SMGL_GL_STACK_OVERFLOW: return "GL_STACK_OVERFLOW";
    case SMGL_GL_STACK_UNDERFLOW: return "GL_STACK_UNDERFLOW";
    case GL_OUT_OF_MEMORY: return "GL_OUT_OF_MEMORY";
    default: return "GL error";
    }
}

static void record_error(GLenum e)
{
    if (st.error == GL_NO_ERROR) {
        st.error = e;
    }
}

/* A GLES 1.1 validation error: the command is ignored, as GLES specifies. */
static void gl_error(int fn, GLenum e, const char *what, unsigned value)
{
    record_error(e);
    diag(SMGL_DIAG_GL_ERROR, fn, "%s: %s (0x%04x); command ignored", gl_error_name(e), what, value);
}

static void backend_check(int fn)
{
    if (!st.debug) {
        return;
    }
    for (int i = 0; i < 8; ++i) {
        GLenum e = glGetError();
        if (e == GL_NO_ERROR) {
            break;
        }
        diag(SMGL_DIAG_BACKEND_GL_ERROR, fn, "GLES2 reported %s (0x%04x)", gl_error_name(e), e);
    }
}

#define SMGL_ENTRY(name)                                                                  \
    do {                                                                                  \
        if (!st.initialized) {                                                            \
            diag(SMGL_DIAG_NOT_INITIALIZED, SMGL_FN_##name, "called before smgl_init(); ignored"); \
            return;                                                                       \
        }                                                                                 \
    } while (0)

/* GLES converts an enum passed through a float parameter by truncation. */
static int enum_from_float(float f, GLenum *out)
{
    if (!(f >= 0.0f && f < 4294967040.0f)) {
        return 0;
    }
    *out = (GLenum)f;
    return 1;
}

static float clamp01(float v)
{
    if (!(v >= 0.0f)) {
        return 0.0f; /* also maps NaN to 0 */
    }
    return v > 1.0f ? 1.0f : v;
}

/* ------------------------------------------------------------------------ */
/* Texture bookkeeping (base format, levels, min filter per texture name).   */

static long tex_index(GLuint name)
{
    for (size_t i = 0; i < st.tex_count; ++i) {
        if (st.tex[i].name == name) {
            return (long)i;
        }
    }
    return -1;
}

static smgl_texrec *tex_get_or_create(GLuint name)
{
    long i = tex_index(name);
    if (i >= 0) {
        return &st.tex[i];
    }
    if (st.tex_count == st.tex_cap) {
        size_t cap = st.tex_cap ? st.tex_cap * 2 : 64;
        smgl_texrec *p = realloc(st.tex, cap * sizeof *p);
        if (p == NULL) {
            return NULL;
        }
        st.tex = p;
        st.tex_cap = cap;
    }
    smgl_texrec *t = &st.tex[st.tex_count++];
    memset(t, 0, sizeof *t);
    t->name = name;
    t->min_filter = GL_NEAREST_MIPMAP_LINEAR; /* GLES 1.1 default */
    return t;
}

static smgl_texrec *tex_bound(void)
{
    long i = tex_index(st.bound_tex);
    return i >= 0 ? &st.tex[i] : NULL;
}

static void tex_remove(GLuint name)
{
    long i = tex_index(name);
    if (i < 0) {
        return;
    }
    st.tex[i] = st.tex[st.tex_count - 1];
    st.tex_count -= 1;
}

static int is_pow2(GLsizei v)
{
    return v > 0 && (v & (v - 1)) == 0;
}

static int log2_floor(GLsizei v)
{
    int n = 0;
    while (v > 1) {
        v >>= 1;
        ++n;
    }
    return n;
}

/* GLES 1.1 §3.7.10: complete when level 0 exists and either the min filter
 * needs no mipmaps or every level down to 1x1 was specified. Per-level
 * sizes/formats are not re-checked (docs/RENDERING.md). */
static int tex_complete(const smgl_texrec *t)
{
    if (t->base_format == 0 || t->width <= 0 || t->height <= 0) {
        return 0;
    }
    if (t->min_filter == GL_NEAREST || t->min_filter == GL_LINEAR) {
        return 1;
    }
    int top = log2_floor(t->width > t->height ? t->width : t->height);
    uint32_t need = (top >= 31) ? 0xFFFFFFFFu : ((1u << (top + 1)) - 1u);
    return (t->levels & need) == need;
}

/* ------------------------------------------------------------------------ */
/* Shader program.                                                            */

static const char k_vertex_src[] =
    "#version 100\n"
    "precision highp float;\n"
    "attribute vec4 a_position;\n"
    "attribute vec4 a_texcoord;\n"
    "uniform mat4 u_modelview;\n"
    "uniform mat4 u_projection;\n"
    "uniform mat4 u_texmatrix;\n"
    "varying vec3 v_texcoord;\n" /* (s, t, q) after the texture matrix */
    "varying float v_eye_z;\n"
    "void main() {\n"
    "  vec4 eye = u_modelview * a_position;\n"
    "  gl_Position = u_projection * eye;\n"
    "  vec4 tc = u_texmatrix * a_texcoord;\n"
    "  v_texcoord = vec3(tc.x, tc.y, tc.w);\n"
    "  v_eye_z = eye.z;\n"
    "  gl_PointSize = 1.0;\n"
    "}\n";

static const char k_fragment_src[] =
    "#version 100\n"
    "#ifdef GL_FRAGMENT_PRECISION_HIGH\n"
    "precision highp float;\n"
    "#else\n"
    "precision mediump float;\n"
    "#endif\n"
    "uniform sampler2D u_sampler;\n"
    "uniform int u_tex_mode;\n"     /* 0 off, 1 MODULATE, 2 REPLACE, 3 DECAL, 4 BLEND, 5 ADD */
    "uniform vec2 u_tex_format;\n"  /* x: base format has colour, y: has alpha */
    "uniform vec4 u_color;\n"       /* current colour, clamped to [0,1] */
    "uniform int u_fog_mode;\n"     /* 0 off, 1 LINEAR, 2 EXP, 3 EXP2 */
    "uniform vec3 u_fog_params;\n"  /* density, end, 1/(end-start) */
    "uniform vec3 u_fog_color;\n"
    "varying vec3 v_texcoord;\n"
    "varying float v_eye_z;\n"
    "void main() {\n"
    "  vec4 cf = u_color;\n"
    "  vec4 c = cf;\n"
    "  if (u_tex_mode != 0) {\n"
    "    vec4 t = texture2DProj(u_sampler, v_texcoord);\n"
    "    bool trgb = u_tex_format.x > 0.5;\n"
    "    bool ta = u_tex_format.y > 0.5;\n"
    "    if (u_tex_mode == 1) {\n"
    "      c.rgb = trgb ? cf.rgb * t.rgb : cf.rgb;\n"
    "      c.a = ta ? cf.a * t.a : cf.a;\n"
    "    } else if (u_tex_mode == 2) {\n"
    "      c.rgb = trgb ? t.rgb : cf.rgb;\n"
    "      c.a = ta ? t.a : cf.a;\n"
    "    } else if (u_tex_mode == 3) {\n"
    "      c.rgb = ta ? mix(cf.rgb, t.rgb, t.a) : t.rgb;\n"
    "      c.a = cf.a;\n"
    "    } else if (u_tex_mode == 4) {\n"
    "      c.rgb = trgb ? cf.rgb * (vec3(1.0) - t.rgb) : cf.rgb;\n"
    "      c.a = ta ? cf.a * t.a : cf.a;\n"
    "    } else {\n"
    "      c.rgb = trgb ? cf.rgb + t.rgb : cf.rgb;\n"
    "      c.a = ta ? cf.a * t.a : cf.a;\n"
    "    }\n"
    "    c = clamp(c, 0.0, 1.0);\n"
    "  }\n"
    "  if (u_fog_mode != 0) {\n"
    "    float z = abs(v_eye_z);\n"
    "    float f;\n"
    "    if (u_fog_mode == 1) {\n"
    "      f = (u_fog_params.y - z) * u_fog_params.z;\n"
    "    } else if (u_fog_mode == 2) {\n"
    "      f = exp(-(u_fog_params.x * z));\n"
    "    } else {\n"
    "      float dz = u_fog_params.x * z;\n"
    "      f = exp(-(dz * dz));\n"
    "    }\n"
    "    f = clamp(f, 0.0, 1.0);\n"
    "    c.rgb = mix(u_fog_color, c.rgb, f);\n"
    "  }\n"
    "  gl_FragColor = c;\n"
    "}\n";

static GLuint compile_shader(GLenum type, const char *src)
{
    GLuint sh = glCreateShader(type);
    if (sh == 0) {
        diag(SMGL_DIAG_BACKEND_GL_ERROR, SMGL_FN_INTERNAL, "glCreateShader failed");
        return 0;
    }
    glShaderSource(sh, 1, &src, NULL);
    glCompileShader(sh);
    GLint ok = GL_FALSE;
    glGetShaderiv(sh, GL_COMPILE_STATUS, &ok);
    if (!ok) {
        char log[1024];
        GLsizei len = 0;
        glGetShaderInfoLog(sh, (GLsizei)sizeof log, &len, log);
        diag(SMGL_DIAG_BACKEND_GL_ERROR, SMGL_FN_INTERNAL, "%s shader compile failed: %.*s",
             type == GL_VERTEX_SHADER ? "vertex" : "fragment", (int)len, log);
        glDeleteShader(sh);
        return 0;
    }
    return sh;
}

static int build_program(void)
{
    st.vs = compile_shader(GL_VERTEX_SHADER, k_vertex_src);
    st.fs = compile_shader(GL_FRAGMENT_SHADER, k_fragment_src);
    if (st.vs == 0 || st.fs == 0) {
        return -1;
    }
    st.program = glCreateProgram();
    if (st.program == 0) {
        diag(SMGL_DIAG_BACKEND_GL_ERROR, SMGL_FN_INTERNAL, "glCreateProgram failed");
        return -1;
    }
    glAttachShader(st.program, st.vs);
    glAttachShader(st.program, st.fs);
    glBindAttribLocation(st.program, SMGL_ATTR_POSITION, "a_position");
    glBindAttribLocation(st.program, SMGL_ATTR_TEXCOORD, "a_texcoord");
    glLinkProgram(st.program);
    GLint ok = GL_FALSE;
    glGetProgramiv(st.program, GL_LINK_STATUS, &ok);
    if (!ok) {
        char log[1024];
        GLsizei len = 0;
        glGetProgramInfoLog(st.program, (GLsizei)sizeof log, &len, log);
        diag(SMGL_DIAG_BACKEND_GL_ERROR, SMGL_FN_INTERNAL, "program link failed: %.*s", (int)len, log);
        return -1;
    }
    st.loc_modelview = glGetUniformLocation(st.program, "u_modelview");
    st.loc_projection = glGetUniformLocation(st.program, "u_projection");
    st.loc_texmatrix = glGetUniformLocation(st.program, "u_texmatrix");
    st.loc_color = glGetUniformLocation(st.program, "u_color");
    st.loc_sampler = glGetUniformLocation(st.program, "u_sampler");
    st.loc_tex_mode = glGetUniformLocation(st.program, "u_tex_mode");
    st.loc_tex_format = glGetUniformLocation(st.program, "u_tex_format");
    st.loc_fog_mode = glGetUniformLocation(st.program, "u_fog_mode");
    st.loc_fog_params = glGetUniformLocation(st.program, "u_fog_params");
    st.loc_fog_color = glGetUniformLocation(st.program, "u_fog_color");
    return 0;
}

static void delete_program(void)
{
    if (st.program != 0) {
        glDeleteProgram(st.program);
    }
    if (st.vs != 0) {
        glDeleteShader(st.vs);
    }
    if (st.fs != 0) {
        glDeleteShader(st.fs);
    }
    st.program = st.vs = st.fs = 0;
}

/* ------------------------------------------------------------------------ */
/* Init / shutdown / introspection.                                           */

static void reset_state(void)
{
    free(st.tex);
    memset(&st, 0, sizeof st);
    memset(g_diag_count, 0, sizeof g_diag_count);
    memset(g_diag_logged, 0, sizeof g_diag_logged);

    smgl_matstack_init(&st.stacks[SMGL_STACK_MODELVIEW], SMGL_MODELVIEW_STACK_DEPTH);
    smgl_matstack_init(&st.stacks[SMGL_STACK_PROJECTION], SMGL_PROJECTION_STACK_DEPTH);
    smgl_matstack_init(&st.stacks[SMGL_STACK_TEXTURE], SMGL_TEXTURE_STACK_DEPTH);
    st.matrix_mode = SMGL_STACK_MODELVIEW;
    st.color[0] = st.color[1] = st.color[2] = st.color[3] = 1.0f;
    st.env_mode = SMGL_GL_MODULATE;
    st.fog_mode = SMGL_GL_EXP;
    st.fog_density = 1.0f;
    st.fog_start = 0.0f;
    st.fog_end = 1.0f;
    st.multisample_enabled = 1; /* GLES 1.1 default: enabled */
    st.shade_model = SMGL_GL_SMOOTH;
    st.hint_perspective = st.hint_point_smooth = st.hint_line_smooth = st.hint_fog = GL_DONT_CARE;
    st.pack_alignment = 4;
    st.dirty = SMGL_DIRTY_ALL;
}

int smgl_init(void)
{
    if (st.initialized) {
        smgl_shutdown();
    }
    reset_state();
    const char *dbg = getenv("SMGL_DEBUG");
    st.debug = (dbg != NULL && dbg[0] != '\0' && strcmp(dbg, "0") != 0);

    if (build_program() != 0) {
        delete_program();
        return -1;
    }
    if (tex_get_or_create(0) == NULL) { /* the default texture object */
        delete_program();
        return -1;
    }
    glGetIntegerv(GL_MAX_TEXTURE_SIZE, &st.max_texture_size);
    if (st.max_texture_size <= 0) {
        st.max_texture_size = 64; /* GLES 1.1 minimum */
    }

    /* Bring the GLES2 objects the emulator owns into the GLES 1.1 initial
     * state; everything else already has identical defaults in both APIs. */
    glUseProgram(st.program);
    glUniform1i(st.loc_sampler, 0);
    glActiveTexture(GL_TEXTURE0);
    glBindTexture(GL_TEXTURE_2D, 0);
    glPixelStorei(GL_PACK_ALIGNMENT, 4);
    glDisableVertexAttribArray(SMGL_ATTR_POSITION);
    glDisableVertexAttribArray(SMGL_ATTR_TEXCOORD);
    glVertexAttrib4f(SMGL_ATTR_POSITION, 0.0f, 0.0f, 0.0f, 1.0f);
    glVertexAttrib4f(SMGL_ATTR_TEXCOORD, 0.0f, 0.0f, 0.0f, 1.0f); /* current texcoord */
    st.initialized = 1;
    backend_check(SMGL_FN_INTERNAL);
    return 0;
}

void smgl_shutdown(void)
{
    if (st.initialized) {
        glUseProgram(0);
        delete_program();
    }
    free(st.tex);
    st.tex = NULL;
    st.tex_count = st.tex_cap = 0;
    st.initialized = 0;
}

sm_GLenum smgl_take_error(void)
{
    GLenum e = st.error;
    st.error = GL_NO_ERROR;
    return e;
}

static int stack_index(sm_GLenum mode)
{
    switch (mode) {
    case SMGL_GL_MODELVIEW: return SMGL_STACK_MODELVIEW;
    case SMGL_GL_PROJECTION: return SMGL_STACK_PROJECTION;
    case SMGL_GL_TEXTURE: return SMGL_STACK_TEXTURE;
    default: return -1;
    }
}

int smgl_get_matrix(sm_GLenum mode, sm_GLfloat out[16])
{
    int i = stack_index(mode);
    if (i < 0 || st.stacks[i].depth < 1) {
        return -1;
    }
    memcpy(out, smgl_matstack_top_const(&st.stacks[i])->m, 16 * sizeof(float));
    return 0;
}

int smgl_get_stack_depth(sm_GLenum mode)
{
    int i = stack_index(mode);
    return (i < 0 || st.stacks[i].depth < 1) ? -1 : st.stacks[i].depth;
}

int smgl_get_tracked(sm_GLenum pname, sm_GLint *out)
{
    static const GLenum stack_enums[3] = { SMGL_GL_MODELVIEW, SMGL_GL_PROJECTION, SMGL_GL_TEXTURE };
    GLint v;
    switch (pname) {
    case SMGL_GL_MATRIX_MODE: v = (GLint)stack_enums[st.matrix_mode]; break;
    case SMGL_GL_SHADE_MODEL: v = (GLint)st.shade_model; break;
    case SMGL_GL_PERSPECTIVE_CORRECTION_HINT: v = (GLint)st.hint_perspective; break;
    case SMGL_GL_POINT_SMOOTH_HINT: v = (GLint)st.hint_point_smooth; break;
    case SMGL_GL_LINE_SMOOTH_HINT: v = (GLint)st.hint_line_smooth; break;
    case SMGL_GL_FOG_HINT: v = (GLint)st.hint_fog; break;
    case SMGL_GL_TEXTURE_ENV_MODE: v = (GLint)st.env_mode; break;
    case SMGL_GL_FOG_MODE: v = (GLint)st.fog_mode; break;
    case SMGL_GL_TEXTURE_BINDING_2D: v = (GLint)st.bound_tex; break;
    case GL_TEXTURE_2D: v = st.tex2d_enabled; break;
    case SMGL_GL_FOG: v = st.fog_enabled; break;
    case SMGL_GL_ALPHA_TEST: v = st.alpha_test_enabled; break;
    case SMGL_GL_MULTISAMPLE: v = st.multisample_enabled; break;
    case SMGL_GL_VERTEX_ARRAY: v = st.va_vertex; break;
    case SMGL_GL_TEXTURE_COORD_ARRAY: v = st.va_texcoord; break;
    case SMGL_GL_COLOR_ARRAY: v = st.va_color; break;
    case SMGL_GL_NORMAL_ARRAY: v = st.va_normal; break;
    case SMGL_GL_POINT_SIZE_ARRAY_OES: v = st.va_point_size; break;
    default: return -1;
    }
    if (out != NULL) {
        *out = v;
    }
    return 0;
}

unsigned long smgl_diag_count(smgl_diag d)
{
    return ((unsigned)d < SMGL_DIAG__COUNT) ? g_diag_count[d] : 0ul;
}

const char *smgl_diag_name(smgl_diag d)
{
    return ((unsigned)d < SMGL_DIAG__COUNT) ? k_diag_names[d] : "unknown";
}

/* ------------------------------------------------------------------------ */
/* Uniform upload.                                                            */

static void flush_uniforms(void)
{
    const unsigned dirty = st.dirty;
    if (dirty & SMGL_DIRTY_MODELVIEW) {
        glUniformMatrix4fv(st.loc_modelview, 1, GL_FALSE,
                           smgl_matstack_top(&st.stacks[SMGL_STACK_MODELVIEW])->m);
    }
    if (dirty & SMGL_DIRTY_PROJECTION) {
        glUniformMatrix4fv(st.loc_projection, 1, GL_FALSE,
                           smgl_matstack_top(&st.stacks[SMGL_STACK_PROJECTION])->m);
    }
    if (dirty & SMGL_DIRTY_TEXMATRIX) {
        glUniformMatrix4fv(st.loc_texmatrix, 1, GL_FALSE,
                           smgl_matstack_top(&st.stacks[SMGL_STACK_TEXTURE])->m);
    }
    if (dirty & SMGL_DIRTY_COLOR) {
        /* GLES 1.1 §2.12.6: colours are clamped to [0,1] (no lighting). */
        glUniform4f(st.loc_color, clamp01(st.color[0]), clamp01(st.color[1]),
                    clamp01(st.color[2]), clamp01(st.color[3]));
    }
    if (dirty & SMGL_DIRTY_TEXTURE) {
        int mode = SMGL_TEXMODE_OFF;
        float has_rgb = 1.0f, has_alpha = 1.0f;
        st.tex_incomplete_active = st.tex_combine_active = st.tex_decal_undefined = 0;
        if (st.tex2d_enabled) {
            const smgl_texrec *t = tex_bound();
            if (t != NULL && tex_complete(t)) {
                switch (st.env_mode) {
                case SMGL_GL_MODULATE: mode = SMGL_TEXMODE_MODULATE; break;
                case GL_REPLACE: mode = SMGL_TEXMODE_REPLACE; break;
                case SMGL_GL_DECAL: mode = SMGL_TEXMODE_DECAL; break;
                case GL_BLEND: mode = SMGL_TEXMODE_BLEND; break;
                case SMGL_GL_ADD: mode = SMGL_TEXMODE_ADD; break;
                default: /* GL_COMBINE */
                    mode = SMGL_TEXMODE_MODULATE;
                    st.tex_combine_active = 1;
                    break;
                }
                has_rgb = (t->base_format != GL_ALPHA) ? 1.0f : 0.0f;
                has_alpha = (t->base_format == GL_ALPHA || t->base_format == GL_LUMINANCE_ALPHA ||
                             t->base_format == GL_RGBA) ? 1.0f : 0.0f;
                if (mode == SMGL_TEXMODE_DECAL && t->base_format != GL_RGB && t->base_format != GL_RGBA) {
                    st.tex_decal_undefined = 1;
                }
            } else {
                /* GLES 1.1 §3.8.10: as if texturing were disabled. */
                st.tex_incomplete_active = 1;
            }
        }
        glUniform1i(st.loc_tex_mode, mode);
        glUniform2f(st.loc_tex_format, has_rgb, has_alpha);
    }
    if (dirty & SMGL_DIRTY_FOG) {
        int mode = SMGL_FOGMODE_OFF;
        if (st.fog_enabled) {
            mode = (st.fog_mode == GL_LINEAR) ? SMGL_FOGMODE_LINEAR
                 : (st.fog_mode == SMGL_GL_EXP2) ? SMGL_FOGMODE_EXP2 : SMGL_FOGMODE_EXP;
        }
        /* end == start is undefined in GL; use scale 1 like Mesa's swrast. */
        const float scale = (st.fog_end != st.fog_start) ? 1.0f / (st.fog_end - st.fog_start) : 1.0f;
        glUniform1i(st.loc_fog_mode, mode);
        glUniform3f(st.loc_fog_params, st.fog_density, st.fog_end, scale);
        glUniform3f(st.loc_fog_color, st.fog_color[0], st.fog_color[1], st.fog_color[2]);
    }
    st.dirty = 0;
}

static void mark_matrix_dirty(int stack)
{
    static const unsigned bits[3] = { SMGL_DIRTY_MODELVIEW, SMGL_DIRTY_PROJECTION, SMGL_DIRTY_TEXMATRIX };
    st.dirty |= bits[stack];
}

static smgl_mat4 *cur_matrix(void)
{
    return smgl_matstack_top(&st.stacks[st.matrix_mode]);
}

/* ------------------------------------------------------------------------ */
/* Matrices.                                                                  */

void smgl_MatrixMode(sm_GLenum mode)
{
    SMGL_ENTRY(MatrixMode);
    int i = stack_index(mode);
    if (i < 0) {
        gl_error(SMGL_FN_MatrixMode, GL_INVALID_ENUM, "mode", mode);
        return;
    }
    st.matrix_mode = i;
}

void smgl_LoadIdentity(void)
{
    SMGL_ENTRY(LoadIdentity);
    smgl_mat4_identity(cur_matrix());
    mark_matrix_dirty(st.matrix_mode);
}

void smgl_MultMatrixf(const sm_GLfloat *m)
{
    SMGL_ENTRY(MultMatrixf);
    if (m == NULL) {
        diag(SMGL_DIAG_NULL_POINTER, SMGL_FN_MultMatrixf, "NULL matrix; ignored");
        return;
    }
    smgl_mat4_mul_array(cur_matrix(), m);
    mark_matrix_dirty(st.matrix_mode);
}

void smgl_Rotatef(sm_GLfloat angle, sm_GLfloat x, sm_GLfloat y, sm_GLfloat z)
{
    SMGL_ENTRY(Rotatef);
    smgl_mat4_rotate(cur_matrix(), angle, x, y, z);
    mark_matrix_dirty(st.matrix_mode);
}

void smgl_Scalef(sm_GLfloat x, sm_GLfloat y, sm_GLfloat z)
{
    SMGL_ENTRY(Scalef);
    smgl_mat4_scale(cur_matrix(), x, y, z);
    mark_matrix_dirty(st.matrix_mode);
}

void smgl_Translatef(sm_GLfloat x, sm_GLfloat y, sm_GLfloat z)
{
    SMGL_ENTRY(Translatef);
    smgl_mat4_translate(cur_matrix(), x, y, z);
    mark_matrix_dirty(st.matrix_mode);
}

void smgl_Frustumf(sm_GLfloat l, sm_GLfloat r, sm_GLfloat b, sm_GLfloat t, sm_GLfloat n, sm_GLfloat f)
{
    SMGL_ENTRY(Frustumf);
    if (smgl_mat4_frustum(cur_matrix(), l, r, b, t, n, f) != 0) {
        gl_error(SMGL_FN_Frustumf, GL_INVALID_VALUE, "n<=0, f<=0, l==r, b==t or n==f", 0u);
        return;
    }
    mark_matrix_dirty(st.matrix_mode);
}

void smgl_Orthof(sm_GLfloat l, sm_GLfloat r, sm_GLfloat b, sm_GLfloat t, sm_GLfloat n, sm_GLfloat f)
{
    SMGL_ENTRY(Orthof);
    if (smgl_mat4_ortho(cur_matrix(), l, r, b, t, n, f) != 0) {
        gl_error(SMGL_FN_Orthof, GL_INVALID_VALUE, "l==r, b==t or n==f", 0u);
        return;
    }
    mark_matrix_dirty(st.matrix_mode);
}

void smgl_PushMatrix(void)
{
    SMGL_ENTRY(PushMatrix);
    static const int es11_min[3] = { SMGL_ES11_MIN_MODELVIEW_STACK_DEPTH,
                                     SMGL_ES11_MIN_PROJECTION_STACK_DEPTH,
                                     SMGL_ES11_MIN_TEXTURE_STACK_DEPTH };
    smgl_matstack *s = &st.stacks[st.matrix_mode];
    if (smgl_matstack_push(s) != 0) {
        record_error(SMGL_GL_STACK_OVERFLOW);
        diag(SMGL_DIAG_STACK_OVERFLOW, SMGL_FN_PushMatrix,
             "GL_STACK_OVERFLOW on stack %d (depth %d); matrix unchanged", st.matrix_mode, s->depth);
        return;
    }
    if (s->depth > es11_min[st.matrix_mode]) {
        diag(SMGL_DIAG_STACK_BEYOND_MINIMUM, SMGL_FN_PushMatrix,
             "stack %d reached depth %d, above the GLES 1.1 guaranteed minimum %d "
             "(a minimal device would have raised GL_STACK_OVERFLOW)",
             st.matrix_mode, s->depth, es11_min[st.matrix_mode]);
    }
}

void smgl_PopMatrix(void)
{
    SMGL_ENTRY(PopMatrix);
    if (smgl_matstack_pop(&st.stacks[st.matrix_mode]) != 0) {
        record_error(SMGL_GL_STACK_UNDERFLOW);
        diag(SMGL_DIAG_STACK_UNDERFLOW, SMGL_FN_PopMatrix,
             "GL_STACK_UNDERFLOW on stack %d; matrix unchanged", st.matrix_mode);
        return;
    }
    mark_matrix_dirty(st.matrix_mode);
}

/* ------------------------------------------------------------------------ */
/* Current colour, texenv, fog, shading, hints.                              */

void smgl_Color4f(sm_GLfloat r, sm_GLfloat g, sm_GLfloat b, sm_GLfloat a)
{
    SMGL_ENTRY(Color4f);
    st.color[0] = r;
    st.color[1] = g;
    st.color[2] = b;
    st.color[3] = a;
    st.dirty |= SMGL_DIRTY_COLOR;
}

void smgl_TexEnvf(sm_GLenum target, sm_GLenum pname, sm_GLfloat param)
{
    SMGL_ENTRY(TexEnvf);
    if (target == SMGL_GL_POINT_SPRITE_OES) {
        diag(SMGL_DIAG_UNSUPPORTED_TEXENV, SMGL_FN_TexEnvf, "GL_POINT_SPRITE_OES target not emulated; ignored");
        return;
    }
    if (target != SMGL_GL_TEXTURE_ENV) {
        gl_error(SMGL_FN_TexEnvf, GL_INVALID_ENUM, "target", target);
        return;
    }
    if (pname == SMGL_GL_TEXTURE_ENV_MODE) {
        GLenum mode = 0;
        if (!enum_from_float(param, &mode)) {
            gl_error(SMGL_FN_TexEnvf, GL_INVALID_ENUM, "GL_TEXTURE_ENV_MODE param", 0u);
            return;
        }
        switch (mode) {
        case SMGL_GL_MODULATE:
        case GL_REPLACE:
        case SMGL_GL_DECAL:
        case GL_BLEND:
        case SMGL_GL_ADD:
            break;
        case SMGL_GL_COMBINE:
            diag(SMGL_DIAG_UNSUPPORTED_TEXENV, SMGL_FN_TexEnvf,
                 "GL_COMBINE is not emulated; draws will use GL_MODULATE");
            break;
        default:
            gl_error(SMGL_FN_TexEnvf, GL_INVALID_ENUM, "GL_TEXTURE_ENV_MODE param", mode);
            return;
        }
        st.env_mode = mode;
        st.dirty |= SMGL_DIRTY_TEXTURE;
        return;
    }
    if (pname == SMGL_GL_RGB_SCALE || pname == SMGL_GL_ALPHA_SCALE) {
        if (param != 1.0f && param != 2.0f && param != 4.0f) {
            gl_error(SMGL_FN_TexEnvf, GL_INVALID_VALUE, "scale must be 1, 2 or 4", pname);
        } else if (param != 1.0f) {
            diag(SMGL_DIAG_UNSUPPORTED_TEXENV, SMGL_FN_TexEnvf,
                 "RGB/ALPHA scale %g not emulated (only used by GL_COMBINE); ignored", (double)param);
        }
        return;
    }
    if (pname == SMGL_GL_COMBINE_RGB || pname == SMGL_GL_COMBINE_ALPHA ||
        (pname >= SMGL_GL_SRC0_RGB && pname <= SMGL_GL_OPERAND2_ALPHA)) {
        diag(SMGL_DIAG_UNSUPPORTED_TEXENV, SMGL_FN_TexEnvf,
             "combiner pname 0x%04x not emulated; ignored", pname);
        return;
    }
    /* GL_TEXTURE_ENV_COLOR needs glTexEnvfv, which the game does not import. */
    gl_error(SMGL_FN_TexEnvf, GL_INVALID_ENUM, "pname", pname);
}

static void fog_param(int fn, sm_GLenum pname, const float *params, int vector)
{
    switch (pname) {
    case SMGL_GL_FOG_MODE: {
        GLenum mode = 0;
        if (!enum_from_float(params[0], &mode) ||
            (mode != GL_LINEAR && mode != SMGL_GL_EXP && mode != SMGL_GL_EXP2)) {
            gl_error(fn, GL_INVALID_ENUM, "GL_FOG_MODE param", mode);
            return;
        }
        st.fog_mode = mode;
        break;
    }
    case SMGL_GL_FOG_DENSITY:
        if (params[0] < 0.0f) {
            gl_error(fn, GL_INVALID_VALUE, "negative GL_FOG_DENSITY", 0u);
            return;
        }
        st.fog_density = params[0];
        break;
    case SMGL_GL_FOG_START:
        st.fog_start = params[0];
        break;
    case SMGL_GL_FOG_END:
        st.fog_end = params[0];
        break;
    case SMGL_GL_FOG_COLOR:
        if (!vector) {
            gl_error(fn, GL_INVALID_ENUM, "GL_FOG_COLOR needs glFogfv", pname);
            return;
        }
        /* GL 1.5 §3.10: the fog colour is clamped to [0,1] when specified. */
        for (int i = 0; i < 4; ++i) {
            st.fog_color[i] = clamp01(params[i]);
        }
        break;
    default:
        gl_error(fn, GL_INVALID_ENUM, "pname", pname);
        return;
    }
    st.dirty |= SMGL_DIRTY_FOG;
}

void smgl_Fogf(sm_GLenum pname, sm_GLfloat param)
{
    SMGL_ENTRY(Fogf);
    fog_param(SMGL_FN_Fogf, pname, &param, 0);
}

void smgl_Fogfv(sm_GLenum pname, const sm_GLfloat *params)
{
    SMGL_ENTRY(Fogfv);
    if (params == NULL) {
        diag(SMGL_DIAG_NULL_POINTER, SMGL_FN_Fogfv, "NULL params; ignored");
        return;
    }
    fog_param(SMGL_FN_Fogfv, pname, params, 1);
}

void smgl_ShadeModel(sm_GLenum mode)
{
    SMGL_ENTRY(ShadeModel);
    if (mode != SMGL_GL_FLAT && mode != SMGL_GL_SMOOTH) {
        gl_error(SMGL_FN_ShadeModel, GL_INVALID_ENUM, "mode", mode);
        return;
    }
    /* Tracked only: without colour arrays and lighting every vertex has the
     * current colour, so FLAT and SMOOTH produce identical fragments. */
    st.shade_model = mode;
}

void smgl_Hint(sm_GLenum target, sm_GLenum mode)
{
    SMGL_ENTRY(Hint);
    if (mode != GL_FASTEST && mode != GL_NICEST && mode != GL_DONT_CARE) {
        gl_error(SMGL_FN_Hint, GL_INVALID_ENUM, "mode", mode);
        return;
    }
    switch (target) {
    case SMGL_GL_PERSPECTIVE_CORRECTION_HINT: st.hint_perspective = mode; break;
    case SMGL_GL_POINT_SMOOTH_HINT: st.hint_point_smooth = mode; break;
    case SMGL_GL_LINE_SMOOTH_HINT: st.hint_line_smooth = mode; break;
    case SMGL_GL_FOG_HINT: st.hint_fog = mode; break;
    case GL_GENERATE_MIPMAP_HINT:
        glHint(target, mode);
        backend_check(SMGL_FN_Hint);
        break;
    default:
        gl_error(SMGL_FN_Hint, GL_INVALID_ENUM, "target", target);
        break;
    }
}

/* ------------------------------------------------------------------------ */
/* Capabilities and client arrays.                                            */

static void set_cap(int fn, sm_GLenum cap, int on)
{
    switch (cap) {
    case GL_TEXTURE_2D:
        st.tex2d_enabled = on;
        st.dirty |= SMGL_DIRTY_TEXTURE;
        return;
    case SMGL_GL_FOG:
        st.fog_enabled = on;
        st.dirty |= SMGL_DIRTY_FOG;
        return;
    case SMGL_GL_ALPHA_TEST:
        st.alpha_test_enabled = on;
        return;
    case SMGL_GL_MULTISAMPLE:
        /* No GLES2 enable exists; multisampling follows the EGL config. */
        st.multisample_enabled = on;
        return;
    case GL_BLEND:
    case GL_DEPTH_TEST:
    case GL_CULL_FACE:
    case GL_SCISSOR_TEST:
    case GL_DITHER:
    case GL_STENCIL_TEST:
    case GL_POLYGON_OFFSET_FILL:
    case GL_SAMPLE_ALPHA_TO_COVERAGE:
    case GL_SAMPLE_COVERAGE:
        if (on) {
            glEnable(cap);
        } else {
            glDisable(cap);
        }
        backend_check(fn);
        return;
    case SMGL_GL_LIGHTING:
    case SMGL_GL_COLOR_MATERIAL:
    case SMGL_GL_NORMALIZE:
    case SMGL_GL_RESCALE_NORMAL:
    case SMGL_GL_POINT_SMOOTH:
    case SMGL_GL_LINE_SMOOTH:
    case SMGL_GL_COLOR_LOGIC_OP:
    case SMGL_GL_SAMPLE_ALPHA_TO_ONE:
    case SMGL_GL_POINT_SPRITE_OES:
        break;
    default:
        if ((cap >= SMGL_GL_LIGHT0 && cap < SMGL_GL_LIGHT0 + 8) ||
            (cap >= SMGL_GL_CLIP_PLANE0 && cap < SMGL_GL_CLIP_PLANE0 + 6)) {
            break;
        }
        gl_error(fn, GL_INVALID_ENUM, "cap", cap);
        return;
    }
    if (on) {
        diag(SMGL_DIAG_UNSUPPORTED_CAP, fn, "cap 0x%04x is not emulated; enabling it has no effect", cap);
    }
}

void smgl_Enable(sm_GLenum cap)
{
    SMGL_ENTRY(Enable);
    set_cap(SMGL_FN_Enable, cap, 1);
}

void smgl_Disable(sm_GLenum cap)
{
    SMGL_ENTRY(Disable);
    set_cap(SMGL_FN_Disable, cap, 0);
}

static void client_state(int fn, sm_GLenum array, int on)
{
    switch (array) {
    case SMGL_GL_VERTEX_ARRAY:
        st.va_vertex = on;
        if (on) {
            glEnableVertexAttribArray(SMGL_ATTR_POSITION);
        } else {
            glDisableVertexAttribArray(SMGL_ATTR_POSITION);
        }
        break;
    case SMGL_GL_TEXTURE_COORD_ARRAY:
        /* Disabled: the attribute's current value (0,0,0,1) is GLES 1.1's
         * current texture coordinate (glMultiTexCoord4f is not imported). */
        st.va_texcoord = on;
        if (on) {
            glEnableVertexAttribArray(SMGL_ATTR_TEXCOORD);
        } else {
            glDisableVertexAttribArray(SMGL_ATTR_TEXCOORD);
        }
        break;
    case SMGL_GL_COLOR_ARRAY:
        st.va_color = on;
        break;
    case SMGL_GL_NORMAL_ARRAY:
        st.va_normal = on;
        break;
    case SMGL_GL_POINT_SIZE_ARRAY_OES:
        st.va_point_size = on;
        break;
    default:
        gl_error(fn, GL_INVALID_ENUM, "array", array);
        return;
    }
    backend_check(fn);
}

void smgl_EnableClientState(sm_GLenum array)
{
    SMGL_ENTRY(EnableClientState);
    client_state(SMGL_FN_EnableClientState, array, 1);
}

void smgl_DisableClientState(sm_GLenum array)
{
    SMGL_ENTRY(DisableClientState);
    client_state(SMGL_FN_DisableClientState, array, 0);
}

static int valid_array_format(int fn, sm_GLint size, sm_GLenum type, sm_GLsizei stride)
{
    if (size < 2 || size > 4) {
        gl_error(fn, GL_INVALID_VALUE, "size", (unsigned)size);
        return 0;
    }
    if (type != GL_BYTE && type != GL_SHORT && type != GL_FIXED && type != GL_FLOAT) {
        gl_error(fn, GL_INVALID_ENUM, "type", type);
        return 0;
    }
    if (stride < 0) {
        gl_error(fn, GL_INVALID_VALUE, "negative stride", 0u);
        return 0;
    }
    return 1;
}

/* GLES 1.1 latches the GL_ARRAY_BUFFER binding at pointer-call time and
 * converts BYTE/SHORT/FIXED without normalisation; glVertexAttribPointer with
 * normalized = GL_FALSE does exactly the same. */
void smgl_VertexPointer(sm_GLint size, sm_GLenum type, sm_GLsizei stride, const void *ptr)
{
    SMGL_ENTRY(VertexPointer);
    if (!valid_array_format(SMGL_FN_VertexPointer, size, type, stride)) {
        return;
    }
    glVertexAttribPointer(SMGL_ATTR_POSITION, size, type, GL_FALSE, stride, ptr);
    backend_check(SMGL_FN_VertexPointer);
}

void smgl_TexCoordPointer(sm_GLint size, sm_GLenum type, sm_GLsizei stride, const void *ptr)
{
    SMGL_ENTRY(TexCoordPointer);
    if (!valid_array_format(SMGL_FN_TexCoordPointer, size, type, stride)) {
        return;
    }
    glVertexAttribPointer(SMGL_ATTR_TEXCOORD, size, type, GL_FALSE, stride, ptr);
    backend_check(SMGL_FN_TexCoordPointer);
}

/* ------------------------------------------------------------------------ */
/* Drawing.                                                                   */

void smgl_DrawElements(sm_GLenum mode, sm_GLsizei count, sm_GLenum type, const void *indices)
{
    SMGL_ENTRY(DrawElements);
    if (mode > GL_TRIANGLE_FAN) {
        gl_error(SMGL_FN_DrawElements, GL_INVALID_ENUM, "mode", mode);
        return;
    }
    if (type != GL_UNSIGNED_BYTE && type != GL_UNSIGNED_SHORT) {
        gl_error(SMGL_FN_DrawElements, GL_INVALID_ENUM, "type", type);
        return;
    }
    if (count < 0) {
        gl_error(SMGL_FN_DrawElements, GL_INVALID_VALUE, "negative count", 0u);
        return;
    }
    if (!st.va_vertex) {
        /* GL 1.x: without the vertex array no vertex is ever issued. */
        diag(SMGL_DIAG_DRAW_WITHOUT_VERTEX_ARRAY, SMGL_FN_DrawElements,
             "GL_VERTEX_ARRAY disabled; nothing is drawn");
        return;
    }
    if (st.va_color) {
        diag(SMGL_DIAG_COLOR_ARRAY_DRAW, SMGL_FN_DrawElements,
             "GL_COLOR_ARRAY enabled but not emulated (no glColorPointer import); current colour used");
    }
    if (st.va_normal) {
        diag(SMGL_DIAG_NORMAL_ARRAY_DRAW, SMGL_FN_DrawElements,
             "GL_NORMAL_ARRAY enabled but not emulated (no glNormalPointer import); ignored");
    }
    if (st.va_point_size) {
        diag(SMGL_DIAG_POINT_SIZE_ARRAY_DRAW, SMGL_FN_DrawElements,
             "GL_POINT_SIZE_ARRAY_OES enabled but not emulated; point size 1 used");
    }
    if (st.alpha_test_enabled) {
        diag(SMGL_DIAG_ALPHA_TEST_DRAW, SMGL_FN_DrawElements,
             "GL_ALPHA_TEST enabled; alpha func stays GL_ALWAYS (glAlphaFunc not imported), so no fragment is rejected");
    }
    glUseProgram(st.program);
    flush_uniforms();
    if (st.tex_incomplete_active) {
        diag(SMGL_DIAG_TEX_INCOMPLETE, SMGL_FN_DrawElements,
             "GL_TEXTURE_2D enabled but texture %u is incomplete; drawn untextured (GLES 1.1 rule)",
             st.bound_tex);
    }
    if (st.tex_combine_active) {
        diag(SMGL_DIAG_UNSUPPORTED_TEXENV, SMGL_FN_DrawElements, "GL_COMBINE drawn as GL_MODULATE");
    }
    if (st.tex_decal_undefined) {
        diag(SMGL_DIAG_UNSUPPORTED_TEXENV, SMGL_FN_DrawElements,
             "GL_DECAL is undefined for this base format; drawn as for RGB");
    }
    glDrawElements(mode, count, type, indices);
    backend_check(SMGL_FN_DrawElements);
}

/* ------------------------------------------------------------------------ */
/* Buffers.                                                                   */

static int valid_buffer_target(sm_GLenum target)
{
    return target == GL_ARRAY_BUFFER || target == GL_ELEMENT_ARRAY_BUFFER;
}

void smgl_BindBuffer(sm_GLenum target, sm_GLuint buffer)
{
    SMGL_ENTRY(BindBuffer);
    if (!valid_buffer_target(target)) {
        gl_error(SMGL_FN_BindBuffer, GL_INVALID_ENUM, "target", target);
        return;
    }
    glBindBuffer(target, buffer);
    backend_check(SMGL_FN_BindBuffer);
}

void smgl_BufferData(sm_GLenum target, sm_GLsizeiptr size, const void *data, sm_GLenum usage)
{
    SMGL_ENTRY(BufferData);
    if (!valid_buffer_target(target)) {
        gl_error(SMGL_FN_BufferData, GL_INVALID_ENUM, "target", target);
        return;
    }
    if (usage != GL_STATIC_DRAW && usage != GL_DYNAMIC_DRAW) { /* no STREAM_DRAW in GLES 1.1 */
        gl_error(SMGL_FN_BufferData, GL_INVALID_ENUM, "usage", usage);
        return;
    }
    if (size < 0) {
        gl_error(SMGL_FN_BufferData, GL_INVALID_VALUE, "negative size", 0u);
        return;
    }
    glBufferData(target, (GLsizeiptr)size, data, usage);
    backend_check(SMGL_FN_BufferData);
}

void smgl_GenBuffers(sm_GLsizei n, sm_GLuint *buffers)
{
    SMGL_ENTRY(GenBuffers);
    if (n < 0) {
        gl_error(SMGL_FN_GenBuffers, GL_INVALID_VALUE, "negative n", 0u);
        return;
    }
    if (n > 0 && buffers == NULL) {
        diag(SMGL_DIAG_NULL_POINTER, SMGL_FN_GenBuffers, "NULL buffers; ignored");
        return;
    }
    glGenBuffers(n, buffers);
    backend_check(SMGL_FN_GenBuffers);
}

/* ------------------------------------------------------------------------ */
/* Textures.                                                                  */

void smgl_GenTextures(sm_GLsizei n, sm_GLuint *textures)
{
    SMGL_ENTRY(GenTextures);
    if (n < 0) {
        gl_error(SMGL_FN_GenTextures, GL_INVALID_VALUE, "negative n", 0u);
        return;
    }
    if (n > 0 && textures == NULL) {
        diag(SMGL_DIAG_NULL_POINTER, SMGL_FN_GenTextures, "NULL textures; ignored");
        return;
    }
    glGenTextures(n, textures);
    backend_check(SMGL_FN_GenTextures);
}

void smgl_DeleteTextures(sm_GLsizei n, const sm_GLuint *textures)
{
    SMGL_ENTRY(DeleteTextures);
    if (n < 0) {
        gl_error(SMGL_FN_DeleteTextures, GL_INVALID_VALUE, "negative n", 0u);
        return;
    }
    if (n > 0 && textures == NULL) {
        diag(SMGL_DIAG_NULL_POINTER, SMGL_FN_DeleteTextures, "NULL textures; ignored");
        return;
    }
    glDeleteTextures(n, textures);
    for (sm_GLsizei i = 0; i < n; ++i) {
        if (textures[i] == 0) {
            continue; /* name 0 is silently ignored */
        }
        tex_remove(textures[i]);
        if (textures[i] == st.bound_tex) {
            st.bound_tex = 0; /* deleting the bound texture binds 0 */
        }
    }
    st.dirty |= SMGL_DIRTY_TEXTURE;
    backend_check(SMGL_FN_DeleteTextures);
}

void smgl_BindTexture(sm_GLenum target, sm_GLuint texture)
{
    SMGL_ENTRY(BindTexture);
    if (target != GL_TEXTURE_2D) {
        gl_error(SMGL_FN_BindTexture, GL_INVALID_ENUM, "target", target);
        return;
    }
    if (tex_get_or_create(texture) == NULL) {
        record_error(GL_OUT_OF_MEMORY);
        diag(SMGL_DIAG_GL_ERROR, SMGL_FN_BindTexture, "out of memory for texture bookkeeping");
        return;
    }
    st.bound_tex = texture;
    glBindTexture(target, texture);
    st.dirty |= SMGL_DIRTY_TEXTURE;
    backend_check(SMGL_FN_BindTexture);
}

static int valid_tex_format(GLenum f)
{
    return f == GL_ALPHA || f == GL_RGB || f == GL_RGBA || f == GL_LUMINANCE || f == GL_LUMINANCE_ALPHA;
}

void smgl_TexImage2D(sm_GLenum target, sm_GLint level, sm_GLint internalformat, sm_GLsizei w,
                     sm_GLsizei h, sm_GLint border, sm_GLenum format, sm_GLenum type,
                     const void *pixels)
{
    SMGL_ENTRY(TexImage2D);
    const int fn = SMGL_FN_TexImage2D;
    if (target != GL_TEXTURE_2D) {
        gl_error(fn, GL_INVALID_ENUM, "target", target);
        return;
    }
    if (!valid_tex_format(format)) {
        gl_error(fn, GL_INVALID_ENUM, "format", format);
        return;
    }
    if (type != GL_UNSIGNED_BYTE && type != GL_UNSIGNED_SHORT_5_6_5 &&
        type != GL_UNSIGNED_SHORT_4_4_4_4 && type != GL_UNSIGNED_SHORT_5_5_5_1) {
        gl_error(fn, GL_INVALID_ENUM, "type", type);
        return;
    }
    if (level < 0 || level > log2_floor(st.max_texture_size)) {
        gl_error(fn, GL_INVALID_VALUE, "level", (unsigned)level);
        return;
    }
    if (!valid_tex_format((GLenum)internalformat)) {
        gl_error(fn, GL_INVALID_VALUE, "internalformat", (unsigned)internalformat);
        return;
    }
    if (w < 0 || h < 0 || w > st.max_texture_size || h > st.max_texture_size) {
        gl_error(fn, GL_INVALID_VALUE, "width/height", 0u);
        return;
    }
    if (border != 0) {
        gl_error(fn, GL_INVALID_VALUE, "border", (unsigned)border);
        return;
    }
    if ((GLenum)internalformat != format) {
        gl_error(fn, GL_INVALID_OPERATION, "internalformat != format", (unsigned)internalformat);
        return;
    }
    if ((type == GL_UNSIGNED_SHORT_5_6_5 && format != GL_RGB) ||
        ((type == GL_UNSIGNED_SHORT_4_4_4_4 || type == GL_UNSIGNED_SHORT_5_5_5_1) && format != GL_RGBA)) {
        gl_error(fn, GL_INVALID_OPERATION, "type/format mismatch", type);
        return;
    }
    if ((w > 0 && !is_pow2(w)) || (h > 0 && !is_pow2(h))) {
        diag(SMGL_DIAG_TEX_NPOT, fn,
             "%dx%d is not a power of two: GLES 1.1 core rejects it (GL_INVALID_VALUE) unless the "
             "device has OES_texture_npot; forwarded as with that extension", w, h);
    }
    smgl_texrec *t = tex_bound();
    glTexImage2D(target, level, internalformat, w, h, border, format, type, pixels);
    if (t != NULL) {
        t->levels |= (1u << level);
        if (level == 0) {
            t->base_format = format;
            t->width = w;
            t->height = h;
            if (t->generate_mipmap && w > 0 && h > 0) {
                glGenerateMipmap(GL_TEXTURE_2D);
                int top = log2_floor(w > h ? w : h);
                t->levels = (top >= 31) ? 0xFFFFFFFFu : ((1u << (top + 1)) - 1u);
            }
        }
    }
    st.dirty |= SMGL_DIRTY_TEXTURE;
    backend_check(fn);
}

void smgl_TexParameteri(sm_GLenum target, sm_GLenum pname, sm_GLint param)
{
    SMGL_ENTRY(TexParameteri);
    const int fn = SMGL_FN_TexParameteri;
    if (target != GL_TEXTURE_2D) {
        gl_error(fn, GL_INVALID_ENUM, "target", target);
        return;
    }
    const GLenum p = (GLenum)param;
    smgl_texrec *t = tex_bound();
    switch (pname) {
    case GL_TEXTURE_MIN_FILTER:
        if (p != GL_NEAREST && p != GL_LINEAR && p != GL_NEAREST_MIPMAP_NEAREST &&
            p != GL_LINEAR_MIPMAP_NEAREST && p != GL_NEAREST_MIPMAP_LINEAR && p != GL_LINEAR_MIPMAP_LINEAR) {
            gl_error(fn, GL_INVALID_ENUM, "GL_TEXTURE_MIN_FILTER param", p);
            return;
        }
        if (t != NULL) {
            t->min_filter = p;
        }
        st.dirty |= SMGL_DIRTY_TEXTURE;
        break;
    case GL_TEXTURE_MAG_FILTER:
        if (p != GL_NEAREST && p != GL_LINEAR) {
            gl_error(fn, GL_INVALID_ENUM, "GL_TEXTURE_MAG_FILTER param", p);
            return;
        }
        break;
    case GL_TEXTURE_WRAP_S:
    case GL_TEXTURE_WRAP_T:
        if (p != GL_REPEAT && p != GL_CLAMP_TO_EDGE) {
            gl_error(fn, GL_INVALID_ENUM, "wrap param", p);
            return;
        }
        break;
    case SMGL_GL_GENERATE_MIPMAP:
        if (t != NULL) {
            t->generate_mipmap = (param != 0);
        }
        if (param != 0) {
            diag(SMGL_DIAG_GENERATE_MIPMAP, fn,
                 "GL_GENERATE_MIPMAP emulated with glGenerateMipmap after each level-0 upload");
        }
        return; /* no GLES2 equivalent parameter */
    default:
        gl_error(fn, GL_INVALID_ENUM, "pname", pname);
        return;
    }
    glTexParameteri(target, pname, param);
    backend_check(fn);
}

void smgl_PixelStorei(sm_GLenum pname, sm_GLint param)
{
    SMGL_ENTRY(PixelStorei);
    if (pname != GL_PACK_ALIGNMENT && pname != GL_UNPACK_ALIGNMENT) {
        gl_error(SMGL_FN_PixelStorei, GL_INVALID_ENUM, "pname", pname);
        return;
    }
    if (param != 1 && param != 2 && param != 4 && param != 8) {
        gl_error(SMGL_FN_PixelStorei, GL_INVALID_VALUE, "alignment", (unsigned)param);
        return;
    }
    if (pname == GL_PACK_ALIGNMENT) {
        st.pack_alignment = param;
    }
    glPixelStorei(pname, param);
    backend_check(SMGL_FN_PixelStorei);
}

void smgl_ReadPixels(sm_GLint x, sm_GLint y, sm_GLsizei w, sm_GLsizei h, sm_GLenum format,
                     sm_GLenum type, void *pixels)
{
    SMGL_ENTRY(ReadPixels);
    const int fn = SMGL_FN_ReadPixels;
    if (w < 0 || h < 0) {
        gl_error(fn, GL_INVALID_VALUE, "negative width/height", 0u);
        return;
    }
    if (format != GL_RGB || type != GL_UNSIGNED_BYTE) {
        glReadPixels(x, y, w, h, format, type, pixels);
        backend_check(fn);
        return;
    }
    /* GL_RGB/GL_UNSIGNED_BYTE (the game's G0ReadFrameBuffer) is only legal in
     * GLES when it is the implementation's preferred read format; read the
     * always-supported RGBA and repack with the current GL_PACK_ALIGNMENT. */
    if (w == 0 || h == 0) {
        return;
    }
    if (pixels == NULL) {
        diag(SMGL_DIAG_NULL_POINTER, fn, "NULL pixels; ignored");
        return;
    }
    if ((size_t)w > SIZE_MAX / 4u / (size_t)h) {
        record_error(GL_OUT_OF_MEMORY);
        return;
    }
    unsigned char *tmp = malloc((size_t)w * (size_t)h * 4u);
    if (tmp == NULL) {
        record_error(GL_OUT_OF_MEMORY);
        diag(SMGL_DIAG_GL_ERROR, fn, "out of memory for RGB readback");
        return;
    }
    diag(SMGL_DIAG_READPIXELS_CONVERTED, fn, "GL_RGB readback served from a GL_RGBA read");
    glPixelStorei(GL_PACK_ALIGNMENT, 4);
    glReadPixels(x, y, w, h, GL_RGBA, GL_UNSIGNED_BYTE, tmp);
    glPixelStorei(GL_PACK_ALIGNMENT, st.pack_alignment);
    const size_t a = (size_t)st.pack_alignment;
    const size_t row = (size_t)w * 3u;
    const size_t stride = (row + a - 1u) / a * a;
    unsigned char *dst = pixels;
    for (sm_GLsizei j = 0; j < h; ++j) {
        const unsigned char *s = tmp + (size_t)j * (size_t)w * 4u;
        unsigned char *d = dst + (size_t)j * stride;
        for (sm_GLsizei i = 0; i < w; ++i) {
            d[3 * i + 0] = s[4 * i + 0];
            d[3 * i + 1] = s[4 * i + 1];
            d[3 * i + 2] = s[4 * i + 2];
        }
    }
    free(tmp);
    backend_check(fn);
}

/* ------------------------------------------------------------------------ */
/* Plain pass-through (GLES2 has the same semantics and valid values).        */

static int valid_blend_src(GLenum f)
{
    return f == GL_ZERO || f == GL_ONE || f == GL_DST_COLOR || f == GL_ONE_MINUS_DST_COLOR ||
           f == GL_SRC_ALPHA || f == GL_ONE_MINUS_SRC_ALPHA || f == GL_DST_ALPHA ||
           f == GL_ONE_MINUS_DST_ALPHA || f == GL_SRC_ALPHA_SATURATE;
}

static int valid_blend_dst(GLenum f)
{
    return f == GL_ZERO || f == GL_ONE || f == GL_SRC_COLOR || f == GL_ONE_MINUS_SRC_COLOR ||
           f == GL_SRC_ALPHA || f == GL_ONE_MINUS_SRC_ALPHA || f == GL_DST_ALPHA ||
           f == GL_ONE_MINUS_DST_ALPHA;
}

void smgl_BlendFunc(sm_GLenum sfactor, sm_GLenum dfactor)
{
    SMGL_ENTRY(BlendFunc);
    /* GLES 1.1 factor sets (GLES2 additionally accepts SRC_COLOR as source,
     * DST_COLOR as destination and the CONSTANT_* factors). */
    if (!valid_blend_src(sfactor)) {
        gl_error(SMGL_FN_BlendFunc, GL_INVALID_ENUM, "sfactor", sfactor);
        return;
    }
    if (!valid_blend_dst(dfactor)) {
        gl_error(SMGL_FN_BlendFunc, GL_INVALID_ENUM, "dfactor", dfactor);
        return;
    }
    glBlendFunc(sfactor, dfactor);
    backend_check(SMGL_FN_BlendFunc);
}

void smgl_Clear(sm_GLbitfield mask)
{
    SMGL_ENTRY(Clear);
    glClear(mask);
    backend_check(SMGL_FN_Clear);
}

void smgl_ClearColor(sm_GLfloat r, sm_GLfloat g, sm_GLfloat b, sm_GLfloat a)
{
    SMGL_ENTRY(ClearColor);
    glClearColor(r, g, b, a);
    backend_check(SMGL_FN_ClearColor);
}

void smgl_ClearDepthf(sm_GLfloat depth)
{
    SMGL_ENTRY(ClearDepthf);
    glClearDepthf(depth);
    backend_check(SMGL_FN_ClearDepthf);
}

void smgl_CullFace(sm_GLenum mode)
{
    SMGL_ENTRY(CullFace);
    glCullFace(mode);
    backend_check(SMGL_FN_CullFace);
}

void smgl_DepthFunc(sm_GLenum func)
{
    SMGL_ENTRY(DepthFunc);
    glDepthFunc(func);
    backend_check(SMGL_FN_DepthFunc);
}

void smgl_DepthMask(sm_GLboolean flag)
{
    SMGL_ENTRY(DepthMask);
    glDepthMask(flag);
    backend_check(SMGL_FN_DepthMask);
}

void smgl_DepthRangef(sm_GLfloat zNear, sm_GLfloat zFar)
{
    SMGL_ENTRY(DepthRangef);
    /* Both APIs clamp to [0,1]: (-0.004, 0.996) becomes (0, 0.996). */
    glDepthRangef(zNear, zFar);
    backend_check(SMGL_FN_DepthRangef);
}

void smgl_Finish(void)
{
    SMGL_ENTRY(Finish);
    glFinish();
    backend_check(SMGL_FN_Finish);
}

void smgl_LineWidth(sm_GLfloat width)
{
    SMGL_ENTRY(LineWidth);
    glLineWidth(width);
    backend_check(SMGL_FN_LineWidth);
}

void smgl_Scissor(sm_GLint x, sm_GLint y, sm_GLsizei w, sm_GLsizei h)
{
    SMGL_ENTRY(Scissor);
    glScissor(x, y, w, h);
    backend_check(SMGL_FN_Scissor);
}

void smgl_Viewport(sm_GLint x, sm_GLint y, sm_GLsizei w, sm_GLsizei h)
{
    SMGL_ENTRY(Viewport);
    glViewport(x, y, w, h);
    backend_check(SMGL_FN_Viewport);
}

/* ------------------------------------------------------------------------ */

static const sm_gl_backend k_backend = {
#define SMGL_BACKEND_ENTRY(name, ret, params, args) .name = smgl_##name,
    SM_GL_FUNCS(SMGL_BACKEND_ENTRY)
#undef SMGL_BACKEND_ENTRY
};

const sm_gl_backend *smgl_backend(void)
{
    return &k_backend;
}
