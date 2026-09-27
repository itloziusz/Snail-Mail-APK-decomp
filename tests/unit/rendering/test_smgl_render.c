/*
 * Rendering tests for the GLES 1.1-on-GLES 2.0 emulation (sm_rendering).
 * Usage: test_smgl_render <scene> [output-dir]. Each scene creates its own
 * 64x64 EGL pbuffer (host/egl_offscreen) and exits 77 (CTest SKIP) when no
 * EGL/GLES2 context can be created.
 *
 * All calls go through the smgl_backend() table, exactly as the AOT runtime
 * would call them. Expected pixels are computed analytically here.
 *
 * Tolerances (per 8-bit channel):
 *  - TOL_Q = 1: a float colour c is stored as round(c * 255); GLES only
 *    requires "nearest" conversion, so an implementation may land one step
 *    off at ties, and products (texel * colour, blending) round once more.
 *  - TOL_FOG = 2: additionally exp() in the fragment shader is only accurate
 *    to a few ulp (GLSL ES precision rules), and the fog blend adds one more
 *    rounding; 2/255 bounds both.
 * Coverage tests put primitive edges on integer window coordinates, i.e.
 * exactly between pixel centres (x + 0.5), so no sample lies on an edge.
 */
#include "sm_rendering/smgl.h"

#include "egl_offscreen.h"

#include <GLES2/gl2.h>

#include <stdint.h>
#include <stdlib.h>
#include <string.h>

#ifdef SMT_HAVE_PNG
#include <png.h>
#endif

#include "smgl_test.h"

#define W 64
#define H 64
#define TOL_Q 1
#define TOL_FOG 2
#define SKIP 77

static const sm_gl_backend *gl;
static unsigned char fb[W * H * 4];

typedef struct rgba { int r, g, b, a; } rgba;

static int q8(float c) /* expected 8-bit value of a float colour */
{
    if (c <= 0.0f) {
        return 0;
    }
    if (c >= 1.0f) {
        return 255;
    }
    return (int)floorf(c * 255.0f + 0.5f);
}

static rgba rgba_f(float r, float g, float b, float a)
{
    rgba c = { q8(r), q8(g), q8(b), q8(a) };
    return c;
}

static void grab(void)
{
    memset(fb, 0xAB, sizeof fb);
    gl->ReadPixels(0, 0, W, H, GL_RGBA, GL_UNSIGNED_BYTE, fb); /* row 0 = bottom */
}

static int px_ok(int x, int y, rgba e, int tol)
{
    const unsigned char *p = &fb[(y * W + x) * 4];
    return abs(p[0] - e.r) <= tol && abs(p[1] - e.g) <= tol && abs(p[2] - e.b) <= tol &&
           abs(p[3] - e.a) <= tol;
}

static void report_px(const char *what, int x, int y, rgba e)
{
    const unsigned char *p = &fb[(y * W + x) * 4];
    fprintf(stderr, "  %s: pixel (%d,%d) = (%d,%d,%d,%d), expected (%d,%d,%d,%d)\n", what, x, y,
            p[0], p[1], p[2], p[3], e.r, e.g, e.b, e.a);
}

/* Check every pixel of the frame: classify(x,y) returns the index into
 * `expect`, or -1 to skip the pixel. One CHECK per call. */
typedef int (*classify_fn)(int x, int y);

static void check_frame(const char *what, classify_fn classify, const rgba *expect, int tol)
{
    int bad = 0, checked = 0;
    for (int y = 0; y < H; ++y) {
        for (int x = 0; x < W; ++x) {
            int k = classify(x, y);
            if (k < 0) {
                continue;
            }
            ++checked;
            if (!px_ok(x, y, expect[k], tol)) {
                if (bad < 3) {
                    report_px(what, x, y, expect[k]);
                }
                ++bad;
            }
        }
    }
    if (bad) {
        fprintf(stderr, "  %s: %d of %d pixels wrong\n", what, bad, checked);
    }
    CHECK(bad == 0 && checked > 0);
}

static int all_pixels(int x, int y)
{
    (void)x;
    (void)y;
    return 0;
}

static void check_all(const char *what, rgba e, int tol)
{
    check_frame(what, all_pixels, &e, tol);
}

static int begin(void)
{
    if (sm_host_egl_create(W, H, 16) != 0) {
        fprintf(stderr, "no EGL/GLES2 context available; skipping\n");
        return SKIP;
    }
    if (smgl_init() != 0) {
        fprintf(stderr, "smgl_init failed\n");
        sm_host_egl_destroy();
        return 1;
    }
    gl = smgl_backend();
    gl->Viewport(0, 0, W, H);
    return 0;
}

static void end(void)
{
    /* The emulator must not have forwarded anything GLES2 rejects. */
    CHECK_EQ_INT(glGetError(), GL_NO_ERROR);
    smgl_shutdown();
    sm_host_egl_destroy();
}

static void clear(float r, float g, float b, float a)
{
    gl->ClearColor(r, g, b, a);
    gl->Clear(GL_COLOR_BUFFER_BIT | GL_DEPTH_BUFFER_BIT);
}

/* Full-screen quad with texcoords, interleaved xyz+uv floats (stride 20, the
 * game's float format), drawn as a strip from client memory. */
static const float k_quad_xyzuv[4][5] = {
    { -1, -1, 0, 0, 0 }, { 1, -1, 0, 1, 0 }, { -1, 1, 0, 0, 1 }, { 1, 1, 0, 1, 1 },
};
static const uint16_t k_strip4[4] = { 0, 1, 2, 3 };

static void draw_quad_z(float z)
{
    float v[4][5];
    memcpy(v, k_quad_xyzuv, sizeof v);
    for (int i = 0; i < 4; ++i) {
        v[i][2] = z;
    }
    gl->EnableClientState(SMGL_GL_VERTEX_ARRAY);
    gl->EnableClientState(SMGL_GL_TEXTURE_COORD_ARRAY);
    gl->VertexPointer(3, GL_FLOAT, 20, &v[0][0]);
    gl->TexCoordPointer(2, GL_FLOAT, 20, &v[0][3]);
    gl->DrawElements(GL_TRIANGLE_STRIP, 4, GL_UNSIGNED_SHORT, k_strip4);
}

static void draw_fullscreen(void)
{
    draw_quad_z(0.0f);
}

/* ---------------------------------------------------------------------- */
/* backend: the dispatch table; calls before smgl_init are ignored.        */

static int scene_backend(void)
{
    const sm_gl_backend *b = smgl_backend();
    CHECK(b != NULL);
    CHECK(b == smgl_backend());
#define SMT_CHECK_ENTRY(name, ret, params, args) CHECK(b->name == smgl_##name);
    SM_GL_FUNCS(SMT_CHECK_ENTRY)
#undef SMT_CHECK_ENTRY
    CHECK_EQ_INT(sizeof(sm_gl_backend) / sizeof(void (*)(void)), 45);

    /* No context and no smgl_init: every entry is a logged no-op. */
    b->Clear(GL_COLOR_BUFFER_BIT);
    b->LoadIdentity();
    CHECK_EQ_INT(smgl_diag_count(SMGL_DIAG_NOT_INITIALIZED), 2);
    return 0;
}

/* ---------------------------------------------------------------------- */
/* (a) clear colour; RGB readback path.                                    */

static int scene_clear(void)
{
    clear(0.25f, 0.5f, 0.75f, 1.0f);
    grab();
    check_all("clear", rgba_f(0.25f, 0.5f, 0.75f, 1.0f), TOL_Q);

    /* The game's G0ReadFrameBuffer reads GL_RGB/GL_UNSIGNED_BYTE; the
     * emulation serves it from an RGBA read and honours GL_PACK_ALIGNMENT
     * (3 px * 3 B = 9 B rows padded to 12 with alignment 4). */
    unsigned char rgb[12 * 2];
    memset(rgb, 0xEE, sizeof rgb);
    gl->ReadPixels(5, 7, 3, 2, GL_RGB, GL_UNSIGNED_BYTE, rgb);
    const rgba e = rgba_f(0.25f, 0.5f, 0.75f, 1.0f);
    for (int row = 0; row < 2; ++row) {
        for (int i = 0; i < 3; ++i) {
            const unsigned char *p = &rgb[row * 12 + i * 3];
            CHECK(abs(p[0] - e.r) <= TOL_Q && abs(p[1] - e.g) <= TOL_Q && abs(p[2] - e.b) <= TOL_Q);
        }
        CHECK(rgb[row * 12 + 9] == 0xEE && rgb[row * 12 + 10] == 0xEE && rgb[row * 12 + 11] == 0xEE);
    }
    gl->PixelStorei(GL_PACK_ALIGNMENT, 1); /* now tightly packed: 9 B rows */
    memset(rgb, 0xEE, sizeof rgb);
    gl->ReadPixels(5, 7, 3, 2, GL_RGB, GL_UNSIGNED_BYTE, rgb);
    CHECK(abs(rgb[9] - e.r) <= TOL_Q && rgb[18] == 0xEE);
    CHECK_EQ_INT(smgl_diag_count(SMGL_DIAG_READPIXELS_CONVERTED), 2);
    CHECK_EQ_INT(smgl_take_error(), 0);
    return 0;
}

/* ---------------------------------------------------------------------- */
/* (b) untextured triangle via client arrays and glColor4f, identity       */
/*     matrices.                                                            */

static int tri_lower_left(int x, int y)
{
    /* Triangle (-1,-1), (1,-1), (-1,1): inside iff x_ndc + y_ndc < 0, i.e.
     * (x+0.5) + (y+0.5) < 64. Centres on the diagonal (x+y == 63) are skipped. */
    if (x + y == 63) {
        return -1;
    }
    return (x + y < 63) ? 0 : 1;
}

static int scene_untextured(void)
{
    clear(0, 0, 0, 0);
    /* Game-style interleaved floats: xyz + uv, stride 20. */
    static const float tri[3][5] = { { -1, -1, 0, 9, 9 }, { 1, -1, 0, 9, 9 }, { -1, 1, 0, 9, 9 } };
    static const unsigned char idx8[3] = { 0, 1, 2 };
    gl->EnableClientState(SMGL_GL_VERTEX_ARRAY);
    gl->VertexPointer(3, GL_FLOAT, 20, tri);
    gl->Color4f(1.0f, 0.6f, 0.2f, 1.0f);
    gl->DrawElements(GL_TRIANGLES, 3, GL_UNSIGNED_BYTE, idx8);
    grab();
    const rgba e1[2] = { rgba_f(1.0f, 0.6f, 0.2f, 1.0f), { 0, 0, 0, 0 } };
    check_frame("triangle", tri_lower_left, e1, TOL_Q);

    /* glColor4f is clamped to [0,1] (no lighting). Full-screen triangle. */
    static const float big[3][3] = { { -1, -1, 0 }, { 3, -1, 0 }, { -1, 3, 0 } };
    gl->VertexPointer(3, GL_FLOAT, 0, big);
    gl->Color4f(2.0f, -1.0f, 0.6f, 1.5f);
    gl->DrawElements(GL_TRIANGLES, 3, GL_UNSIGNED_BYTE, idx8);
    grab();
    check_all("clamped colour", rgba_f(1.0f, 0.0f, 0.6f, 1.0f), TOL_Q);

    /* Without GL_VERTEX_ARRAY nothing is drawn (GL 1.x). */
    gl->DisableClientState(SMGL_GL_VERTEX_ARRAY);
    gl->Color4f(0, 0, 1, 1);
    gl->DrawElements(GL_TRIANGLES, 3, GL_UNSIGNED_BYTE, idx8);
    grab();
    check_all("no vertex array", rgba_f(1.0f, 0.0f, 0.6f, 1.0f), TOL_Q);
    CHECK_EQ_INT(smgl_diag_count(SMGL_DIAG_DRAW_WITHOUT_VERTEX_ARRAY), 1);
    CHECK_EQ_INT(smgl_take_error(), 0);
    return 0;
}

/* ---------------------------------------------------------------------- */
/* (c) glOrthof + glTranslatef/glScalef/glRotatef positioning.              */

static int ortho_rect1(int x, int y)
{
    /* eye x in [16,24], y in [8,12]; window y = 64 - y_eye -> [52,56]. */
    return (x >= 16 && x < 24 && y >= 52 && y < 56) ? 0 : 1;
}

static int ortho_rect2(int x, int y)
{
    /* eye x in [24,32], y in [32,48] -> window x [24,32], y [16,32]. */
    return (x >= 24 && x < 32 && y >= 16 && y < 32) ? 0 : 1;
}

static int scene_ortho(void)
{
    static const float unit[4][3] = { { 0, 0, 0 }, { 1, 0, 0 }, { 0, 1, 0 }, { 1, 1, 0 } };
    /* The game's 2D projection: glOrthof(0, W, H, 0, -1, 1) (y down). */
    gl->MatrixMode(SMGL_GL_PROJECTION);
    gl->LoadIdentity();
    gl->Orthof(0.0f, (float)W, (float)H, 0.0f, -1.0f, 1.0f);
    gl->MatrixMode(SMGL_GL_MODELVIEW);
    gl->LoadIdentity();
    gl->Translatef(16.0f, 8.0f, 0.0f);
    gl->Scalef(8.0f, 4.0f, 0.0f); /* z scale 0 as in G0FontRenderStart */
    clear(0, 0, 0, 1);
    gl->EnableClientState(SMGL_GL_VERTEX_ARRAY);
    gl->VertexPointer(3, GL_FLOAT, 0, unit);
    gl->Color4f(0.0f, 1.0f, 0.0f, 1.0f);
    gl->DrawElements(GL_TRIANGLE_STRIP, 4, GL_UNSIGNED_SHORT, k_strip4);
    grab();
    const rgba e[2] = { { 0, 255, 0, 255 }, { 0, 0, 0, 255 } };
    check_frame("ortho translate/scale", ortho_rect1, e, TOL_Q);

    /* Rz(90) maps (x,y) -> (-y,x): the 16x8 quad becomes x in [-8,0],
     * y in [0,16], then translated by (32,32). */
    gl->LoadIdentity();
    gl->Translatef(32.0f, 32.0f, 0.0f);
    gl->Rotatef(90.0f, 0.0f, 0.0f, 1.0f);
    gl->Scalef(16.0f, 8.0f, 1.0f);
    clear(0, 0, 0, 1);
    gl->DrawElements(GL_TRIANGLE_STRIP, 4, GL_UNSIGNED_SHORT, k_strip4);
    grab();
    check_frame("ortho rotate", ortho_rect2, e, TOL_Q);
    CHECK_EQ_INT(smgl_take_error(), 0);
    return 0;
}

/* ---------------------------------------------------------------------- */
/* (d) VBO + element buffer, GL_SHORT stride-10 vertices, glScalef(1/128).  */

static int vbo_rects(int x, int y)
{
    /* Quad A: x_ndc [-0.75,-0.25] -> window x [8,24]; quad B: [0.25,0.75]
     * -> [40,56]; both y_ndc [-0.5,0.5] -> [16,48]. The degenerate strip
     * joins must not cover the gap. */
    const int in_y = (y >= 16 && y < 48);
    if (in_y && ((x >= 8 && x < 24) || (x >= 40 && x < 56))) {
        return 0;
    }
    return 1;
}

static int scene_vbo_short(void)
{
    /* x, y, z, u, v as int16 (10 bytes), quads in perimeter order as the
     * game's shared index buffer {i,i,i+1,i+3,i+2,i+2} expects. */
    static const int16_t verts[8][5] = {
        { -96, -64, 0, 0, 0 }, { -32, -64, 0, 1, 0 }, { -32, 64, 0, 1, 1 }, { -96, 64, 0, 0, 1 },
        { 32, -64, 0, 0, 0 },  { 96, -64, 0, 1, 0 },  { 96, 64, 0, 1, 1 },  { 32, 64, 0, 0, 1 },
    };
    /* Two leading junk indices so the draw uses a non-zero offset. */
    static const uint16_t idx[14] = { 7, 7, 0, 0, 1, 3, 2, 2, 4, 4, 5, 7, 6, 6 };
    _Static_assert(sizeof verts[0] == 10, "stride 10");

    sm_GLuint bufs[2] = { 0, 0 };
    gl->GenBuffers(2, bufs);
    CHECK(bufs[0] != 0 && bufs[1] != 0);
    gl->BindBuffer(GL_ARRAY_BUFFER, bufs[0]);
    gl->BufferData(GL_ARRAY_BUFFER, (sm_GLsizeiptr)sizeof verts, verts, GL_STATIC_DRAW);
    gl->BindBuffer(GL_ELEMENT_ARRAY_BUFFER, bufs[1]);
    gl->BufferData(GL_ELEMENT_ARRAY_BUFFER, (sm_GLsizeiptr)sizeof idx, idx, GL_STATIC_DRAW);

    gl->EnableClientState(SMGL_GL_VERTEX_ARRAY);
    gl->EnableClientState(SMGL_GL_TEXTURE_COORD_ARRAY);
    gl->VertexPointer(3, GL_SHORT, 10, (const void *)(uintptr_t)0);
    gl->TexCoordPointer(2, GL_SHORT, 10, (const void *)(uintptr_t)6);
    /* GLES 1.1 latched the VBO at pointer time; unbinding must not matter. */
    gl->BindBuffer(GL_ARRAY_BUFFER, 0);

    gl->MatrixMode(SMGL_GL_MODELVIEW);
    gl->LoadIdentity();
    gl->Scalef(1.0f / 128.0f, 1.0f / 128.0f, 1.0f / 128.0f); /* G0RenderObject */
    gl->Color4f(0.0f, 1.0f, 0.0f, 1.0f);
    clear(0, 0, 0, 1);
    gl->DrawElements(GL_TRIANGLE_STRIP, 12, GL_UNSIGNED_SHORT, (const void *)(uintptr_t)4);
    grab();
    const rgba e[2] = { { 0, 255, 0, 255 }, { 0, 0, 0, 255 } };
    check_frame("vbo short", vbo_rects, e, TOL_Q);
    gl->BindBuffer(GL_ELEMENT_ARRAY_BUFFER, 0);
    CHECK_EQ_INT(smgl_take_error(), 0);
    return 0;
}

/* ---------------------------------------------------------------------- */
/* (e) RGBA texture, MODULATE, texture-matrix translate; RGB texture;      */
/*     incomplete texture = texturing off.                                 */

/* 2x2 RGBA, row 0 (t < 0.5) first: red, green / blue, white@128. */
static const unsigned char k_tex2x2[16] = {
    255, 0, 0, 255,   0, 255, 0, 255,
    0, 0, 255, 255,   255, 255, 255, 128,
};
static const float k_mod_color[4] = { 1.0f, 0.6f, 1.0f, 0.8f };

static int texel_for_pixel(int x, int y)
{
    /* s = (x+0.5)/64 shifted by the texture matrix: s' = s + 0.5, REPEAT,
     * GL_NEAREST: column = floor(frac(s') * 2), row = floor(t * 2). The
     * closest texel boundary is half a pixel away from every centre. */
    const float s = (x + 0.5f) / 64.0f + 0.5f;
    const float t = (y + 0.5f) / 64.0f;
    const int col = (int)floorf((s - floorf(s)) * 2.0f);
    const int row = (int)floorf(t * 2.0f);
    return row * 2 + col;
}

static rgba modulate_expect(int texel)
{
    const unsigned char *p = &k_tex2x2[texel * 4];
    return rgba_f(k_mod_color[0] * (p[0] / 255.0f), k_mod_color[1] * (p[1] / 255.0f),
                  k_mod_color[2] * (p[2] / 255.0f), k_mod_color[3] * (p[3] / 255.0f));
}

static int scene_texture(void)
{
    sm_GLuint tex[3] = { 0, 0, 0 };
    gl->GenTextures(3, tex);
    gl->BindTexture(GL_TEXTURE_2D, tex[0]);
    gl->PixelStorei(GL_UNPACK_ALIGNMENT, 1);
    gl->TexImage2D(GL_TEXTURE_2D, 0, GL_RGBA, 2, 2, 0, GL_RGBA, GL_UNSIGNED_BYTE, k_tex2x2);
    gl->TexParameteri(GL_TEXTURE_2D, GL_TEXTURE_MIN_FILTER, GL_NEAREST);
    gl->TexParameteri(GL_TEXTURE_2D, GL_TEXTURE_MAG_FILTER, GL_NEAREST);
    gl->TexParameteri(GL_TEXTURE_2D, GL_TEXTURE_WRAP_S, GL_REPEAT);
    gl->TexParameteri(GL_TEXTURE_2D, GL_TEXTURE_WRAP_T, GL_REPEAT);
    gl->Enable(GL_TEXTURE_2D);
    gl->TexEnvf(SMGL_GL_TEXTURE_ENV, SMGL_GL_TEXTURE_ENV_MODE, (float)SMGL_GL_MODULATE); /* 8448.0f */

    gl->MatrixMode(SMGL_GL_TEXTURE);
    gl->LoadIdentity();
    gl->Translatef(0.5f, 0.0f, 0.0f);
    gl->MatrixMode(SMGL_GL_MODELVIEW);
    gl->Color4f(k_mod_color[0], k_mod_color[1], k_mod_color[2], k_mod_color[3]);
    clear(0, 0, 0, 0);
    draw_fullscreen();
    grab();
    {
        int bad = 0;
        for (int y = 0; y < H; ++y) {
            for (int x = 0; x < W; ++x) {
                const rgba e = modulate_expect(texel_for_pixel(x, y));
                if (!px_ok(x, y, e, TOL_Q)) {
                    if (bad < 3) {
                        report_px("modulate", x, y, e);
                    }
                    ++bad;
                }
            }
        }
        CHECK(bad == 0);
        /* Spot values, worked out by hand: bottom-left samples texel (1,0)
         * green -> (0, .6*255, 0, .8*255) = (0,153,0,204); top-left samples
         * (1,1) white@128 -> (255,153,255, .8*128) = (255,153,255,102). */
        const rgba bl = { 0, 153, 0, 204 }, br = { 255, 0, 0, 204 };
        const rgba tl = { 255, 153, 255, 102 }, tr = { 0, 0, 255, 204 };
        CHECK(px_ok(10, 10, bl, TOL_Q));
        CHECK(px_ok(50, 10, br, TOL_Q));
        CHECK(px_ok(10, 50, tl, TOL_Q));
        CHECK(px_ok(50, 50, tr, TOL_Q));
    }
    gl->MatrixMode(SMGL_GL_TEXTURE);
    gl->LoadIdentity();
    gl->MatrixMode(SMGL_GL_MODELVIEW);

    /* GL_RGB texture: At = 1, so MODULATE keeps Af; REPLACE gives Ct, Af. */
    static const unsigned char rgb1[3] = { 128, 64, 32 };
    gl->BindTexture(GL_TEXTURE_2D, tex[1]);
    gl->TexImage2D(GL_TEXTURE_2D, 0, GL_RGB, 1, 1, 0, GL_RGB, GL_UNSIGNED_BYTE, rgb1);
    gl->TexParameteri(GL_TEXTURE_2D, GL_TEXTURE_MIN_FILTER, GL_LINEAR);
    gl->Color4f(1.0f, 1.0f, 1.0f, 0.8f);
    draw_fullscreen();
    grab();
    const rgba rgb_mod = { 128, 64, 32, 204 };
    check_all("RGB modulate", rgb_mod, TOL_Q);
    gl->TexEnvf(SMGL_GL_TEXTURE_ENV, SMGL_GL_TEXTURE_ENV_MODE, (float)GL_REPLACE);
    gl->Color4f(0.2f, 0.2f, 0.2f, 0.8f);
    draw_fullscreen();
    grab();
    check_all("RGB replace", rgb_mod, TOL_Q);
    gl->TexEnvf(SMGL_GL_TEXTURE_ENV, SMGL_GL_TEXTURE_ENV_MODE, (float)SMGL_GL_MODULATE);

    /* The current colour is clamped before texturing: (2,-1,2,1) acts as
     * (1,0,1,1), so MODULATE gives the texel (128,0,32); unclamped it would
     * give (255,0,64). Untextured draws cannot show this (the framebuffer
     * clamps anyway). */
    gl->Color4f(2.0f, -1.0f, 2.0f, 1.0f);
    draw_fullscreen();
    grab();
    const rgba clamped_mod = { 128, 0, 32, 255 };
    check_all("colour clamped before MODULATE", clamped_mod, TOL_Q);

    /* Incomplete texture: 2x2 with only level 0 while the default min filter
     * (GL_NEAREST_MIPMAP_LINEAR) needs level 1 too. GLES 1.1 draws as if
     * texturing were disabled (GLES2 would sample black). A 1x1 image would
     * be complete: its mipmap chain is level 0 alone. */
    static const unsigned char black[16] = { 0, 0, 0, 255, 0, 0, 0, 255, 0, 0, 0, 255, 0, 0, 0, 255 };
    gl->BindTexture(GL_TEXTURE_2D, tex[2]);
    gl->TexImage2D(GL_TEXTURE_2D, 0, GL_RGBA, 2, 2, 0, GL_RGBA, GL_UNSIGNED_BYTE, black);
    gl->Color4f(0.2f, 0.4f, 0.6f, 1.0f);
    draw_fullscreen();
    grab();
    check_all("incomplete = untextured", rgba_f(0.2f, 0.4f, 0.6f, 1.0f), TOL_Q);
    CHECK(smgl_diag_count(SMGL_DIAG_TEX_INCOMPLETE) >= 1);
    gl->TexParameteri(GL_TEXTURE_2D, GL_TEXTURE_MIN_FILTER, GL_LINEAR); /* now complete */
    draw_fullscreen();
    grab();
    const rgba black_px = { 0, 0, 0, 255 };
    check_all("complete black texture", black_px, TOL_Q);

    /* Deleting the bound texture rebinds 0, which has no image. */
    gl->DeleteTextures(1, &tex[2]);
    sm_GLint bound = -1;
    CHECK_EQ_INT(smgl_get_tracked(SMGL_GL_TEXTURE_BINDING_2D, &bound), 0);
    CHECK_EQ_INT(bound, 0);
    draw_fullscreen();
    grab();
    check_all("deleted texture", rgba_f(0.2f, 0.4f, 0.6f, 1.0f), TOL_Q);
    gl->DeleteTextures(2, tex);
    CHECK_EQ_INT(smgl_take_error(), 0);
    return 0;
}

/* ---------------------------------------------------------------------- */
/* (f) fog EXP / EXP2 / LINEAR at a known eye depth.                        */

static void fog_setup_view(float eye_z)
{
    gl->MatrixMode(SMGL_GL_PROJECTION);
    gl->LoadIdentity();
    gl->Frustumf(-1.0f, 1.0f, -1.0f, 1.0f, 1.0f, 100.0f);
    gl->MatrixMode(SMGL_GL_MODELVIEW);
    gl->LoadIdentity();
    gl->Translatef(0.0f, 0.0f, eye_z); /* the quad sits at z_eye = eye_z */
}

static void draw_big_quad(void)
{
    /* Covers the whole view at |z_eye| <= 100 (x_ndc = x / |z_eye|). */
    static const float big[4][5] = {
        { -200, -200, 0, 0, 0 }, { 200, -200, 0, 1, 0 }, { -200, 200, 0, 0, 1 }, { 200, 200, 0, 1, 1 },
    };
    gl->EnableClientState(SMGL_GL_VERTEX_ARRAY);
    gl->EnableClientState(SMGL_GL_TEXTURE_COORD_ARRAY);
    gl->VertexPointer(3, GL_FLOAT, 20, &big[0][0]);
    gl->TexCoordPointer(2, GL_FLOAT, 20, &big[0][3]);
    gl->DrawElements(GL_TRIANGLE_STRIP, 4, GL_UNSIGNED_SHORT, k_strip4);
}

/* C = f * Cfrag + (1 - f) * Cfog on RGB; alpha unchanged. */
static rgba fog_expect(float f, const float c[4], const float fogc[3])
{
    return rgba_f(f * c[0] + (1.0f - f) * fogc[0], f * c[1] + (1.0f - f) * fogc[1],
                  f * c[2] + (1.0f - f) * fogc[2], c[3]);
}

static int scene_fog(void)
{
    static const float fog_color[4] = { 0.0f, 0.0f, 1.0f, 1.0f };
    static const float col[4] = { 1.0f, 0.0f, 0.0f, 0.8f };
    gl->Enable(SMGL_GL_FOG);
    gl->Fogfv(SMGL_GL_FOG_COLOR, fog_color);
    gl->Color4f(col[0], col[1], col[2], col[3]);

    /* GL_EXP (default mode) with the game's density 0.02f (0x3ca3d70a) at
     * z_eye = -25: f = exp(-0.02 * 25) = exp(-0.5) = 0.60653 ->
     * (155, 0, 100, 204). */
    union { uint32_t u; float f; } game_density = { 0x3ca3d70au };
    gl->Fogf(SMGL_GL_FOG_DENSITY, game_density.f);
    fog_setup_view(-25.0f);
    clear(0, 0, 0, 0);
    draw_big_quad();
    grab();
    const rgba exp_e = fog_expect(expf(-0.5f), col, fog_color);
    CHECK_EQ_INT(exp_e.r, 155);
    CHECK_EQ_INT(exp_e.b, 100);
    check_all("fog EXP d=0.02 z=-25", exp_e, TOL_FOG);
    sm_GLint mode = 0;
    CHECK_EQ_INT(smgl_get_tracked(SMGL_GL_FOG_MODE, &mode), 0);
    CHECK_EQ_INT(mode, SMGL_GL_EXP);

    /* EXP, density 0.05, z_eye = -10: same f. */
    gl->Fogf(SMGL_GL_FOG_DENSITY, 0.05f);
    fog_setup_view(-10.0f);
    draw_big_quad();
    grab();
    check_all("fog EXP d=0.05 z=-10", exp_e, TOL_FOG);

    /* EXP2: f = exp(-(0.05 * 10)^2) = exp(-0.25) = 0.77880 -> (199, 0, 56). */
    gl->Fogf(SMGL_GL_FOG_MODE, (float)SMGL_GL_EXP2);
    draw_big_quad();
    grab();
    check_all("fog EXP2", fog_expect(expf(-0.25f), col, fog_color), TOL_FOG);

    /* LINEAR, start 0, end 40, z_eye = -10: f = 30/40 = 0.75, applied after
     * MODULATE with a 1x1 RGB texture (255,128,0): Cr = (1, 128/255, 0). */
    gl->Fogf(SMGL_GL_FOG_MODE, (float)GL_LINEAR);
    gl->Fogf(SMGL_GL_FOG_START, 0.0f);
    gl->Fogf(SMGL_GL_FOG_END, 40.0f);
    static const unsigned char orange[3] = { 255, 128, 0 };
    sm_GLuint tex = 0;
    gl->GenTextures(1, &tex);
    gl->BindTexture(GL_TEXTURE_2D, tex);
    gl->PixelStorei(GL_UNPACK_ALIGNMENT, 1);
    gl->TexImage2D(GL_TEXTURE_2D, 0, GL_RGB, 1, 1, 0, GL_RGB, GL_UNSIGNED_BYTE, orange);
    gl->TexParameteri(GL_TEXTURE_2D, GL_TEXTURE_MIN_FILTER, GL_LINEAR);
    gl->Enable(GL_TEXTURE_2D);
    gl->Color4f(1.0f, 1.0f, 1.0f, 1.0f);
    draw_big_quad();
    grab();
    const float textured[4] = { 1.0f, 128.0f / 255.0f, 0.0f, 1.0f };
    check_all("fog LINEAR textured", fog_expect(0.75f, textured, fog_color), TOL_FOG);
    gl->Disable(GL_TEXTURE_2D);
    gl->DeleteTextures(1, &tex);

    /* Fog off: plain colour. */
    gl->Disable(SMGL_GL_FOG);
    gl->Color4f(col[0], col[1], col[2], col[3]);
    draw_big_quad();
    grab();
    check_all("fog disabled", rgba_f(col[0], col[1], col[2], col[3]), TOL_Q);

    /* Invalid fog parameters are rejected and change nothing. */
    gl->Fogf(SMGL_GL_FOG_DENSITY, -1.0f);
    CHECK_EQ_INT(smgl_take_error(), GL_INVALID_VALUE);
    gl->Fogf(SMGL_GL_FOG_COLOR, 1.0f);
    CHECK_EQ_INT(smgl_take_error(), GL_INVALID_ENUM);
    gl->Fogf(SMGL_GL_FOG_MODE, 1234.0f);
    CHECK_EQ_INT(smgl_take_error(), GL_INVALID_ENUM);
    CHECK_EQ_INT(smgl_get_tracked(SMGL_GL_FOG_MODE, &mode), 0);
    CHECK_EQ_INT(mode, GL_LINEAR);
    return 0;
}

/* ---------------------------------------------------------------------- */
/* (g) glPushMatrix / glPopMatrix restore; overflow and underflow.          */

static int pushpop_rects(int x, int y)
{
    /* Green: centre x_ndc 0.5, half-size 0.25 -> window x [40,56];
     * red: centre -0.5 -> [8,24]; both y_ndc [-0.25,0.25] -> [24,40]. */
    if (y >= 24 && y < 40) {
        if (x >= 40 && x < 56) {
            return 0;
        }
        if (x >= 8 && x < 24) {
            return 1;
        }
    }
    return 2;
}

static int scene_pushpop(void)
{
    float before[16], after[16];
    gl->MatrixMode(SMGL_GL_MODELVIEW);
    gl->LoadIdentity();
    gl->Translatef(-0.5f, 0.0f, 0.0f);
    CHECK_EQ_INT(smgl_get_matrix(SMGL_GL_MODELVIEW, before), 0);
    gl->PushMatrix();
    CHECK_EQ_INT(smgl_get_stack_depth(SMGL_GL_MODELVIEW), 2);
    gl->Translatef(1.0f, 0.0f, 0.0f);
    gl->Scalef(0.25f, 0.25f, 1.0f);
    clear(0, 0, 0, 1);
    gl->Color4f(0, 1, 0, 1);
    draw_fullscreen();
    gl->PopMatrix();
    CHECK_EQ_INT(smgl_get_matrix(SMGL_GL_MODELVIEW, after), 0);
    CHECK(memcmp(before, after, sizeof before) == 0);
    gl->Scalef(0.25f, 0.25f, 1.0f);
    gl->Color4f(1, 0, 0, 1);
    draw_fullscreen();
    grab();
    const rgba e[3] = { { 0, 255, 0, 255 }, { 255, 0, 0, 255 }, { 0, 0, 0, 255 } };
    check_frame("push/pop", pushpop_rects, e, TOL_Q);

    /* PROJECTION holds 4 here (GLES 1.1 guarantees 2): depth 3 and 4 are
     * logged as beyond the minimum; the 4th push overflows. */
    gl->MatrixMode(SMGL_GL_PROJECTION);
    gl->LoadIdentity();
    gl->Translatef(0.0f, 0.0f, 0.5f);
    gl->PushMatrix();
    gl->PushMatrix();
    gl->PushMatrix();
    CHECK_EQ_INT(smgl_get_stack_depth(SMGL_GL_PROJECTION), SMGL_PROJECTION_STACK_DEPTH);
    CHECK_EQ_INT(smgl_take_error(), 0);
    CHECK(smgl_diag_count(SMGL_DIAG_STACK_BEYOND_MINIMUM) >= 1);
    gl->Scalef(2.0f, 2.0f, 2.0f);
    CHECK_EQ_INT(smgl_get_matrix(SMGL_GL_PROJECTION, before), 0);
    gl->PushMatrix();
    CHECK_EQ_INT(smgl_take_error(), SMGL_GL_STACK_OVERFLOW);
    CHECK_EQ_INT(smgl_get_stack_depth(SMGL_GL_PROJECTION), SMGL_PROJECTION_STACK_DEPTH);
    CHECK_EQ_INT(smgl_get_matrix(SMGL_GL_PROJECTION, after), 0);
    CHECK(memcmp(before, after, sizeof before) == 0);
    gl->PopMatrix();
    gl->PopMatrix();
    gl->PopMatrix();
    CHECK_EQ_INT(smgl_take_error(), 0);
    CHECK_EQ_INT(smgl_get_matrix(SMGL_GL_PROJECTION, before), 0);
    gl->PopMatrix();
    CHECK_EQ_INT(smgl_take_error(), SMGL_GL_STACK_UNDERFLOW);
    CHECK_EQ_INT(smgl_get_stack_depth(SMGL_GL_PROJECTION), 1);
    CHECK_EQ_INT(smgl_get_matrix(SMGL_GL_PROJECTION, after), 0);
    CHECK(memcmp(before, after, sizeof before) == 0);
    CHECK(after[14] == 0.5f); /* the translate survived */
    CHECK_EQ_INT(smgl_diag_count(SMGL_DIAG_STACK_OVERFLOW), 1);
    CHECK_EQ_INT(smgl_diag_count(SMGL_DIAG_STACK_UNDERFLOW), 1);

    /* TEXTURE stack: push/pop restores the identity. */
    gl->MatrixMode(SMGL_GL_TEXTURE);
    gl->PushMatrix();
    gl->Translatef(0.5f, 0.0f, 0.0f);
    gl->PopMatrix();
    CHECK_EQ_INT(smgl_get_matrix(SMGL_GL_TEXTURE, after), 0);
    CHECK(after[0] == 1.0f && after[12] == 0.0f && after[15] == 1.0f);
    CHECK_EQ_INT(smgl_get_stack_depth(SMGL_GL_TEXTURE), 1);
    CHECK_EQ_INT(smgl_take_error(), 0);
    return 0;
}

/* ---------------------------------------------------------------------- */
/* (h) blending.                                                             */

static int scene_blend(void)
{
    /* GL_ONE, GL_ONE (G0SetBlend mode 3): dst + src, saturating. Colours
     * are exact multiples of 1/255 so the only rounding is the final one. */
    clear(40 / 255.0f, 100 / 255.0f, 200 / 255.0f, 10 / 255.0f);
    gl->Enable(GL_BLEND);
    gl->BlendFunc(GL_ONE, GL_ONE);
    gl->Color4f(30 / 255.0f, 100 / 255.0f, 100 / 255.0f, 20 / 255.0f);
    draw_fullscreen();
    grab();
    const rgba add = { 70, 200, 255, 30 };
    check_all("blend ONE,ONE", add, TOL_Q);

    /* GL_SRC_ALPHA, GL_ONE_MINUS_SRC_ALPHA over opaque blue:
     * C = a*src + (1-a)*dst per channel, alpha likewise. */
    clear(0.0f, 0.0f, 1.0f, 1.0f);
    gl->BlendFunc(GL_SRC_ALPHA, GL_ONE_MINUS_SRC_ALPHA);
    const float a = 64 / 255.0f;
    gl->Color4f(1.0f, 0.0f, 0.0f, a);
    draw_fullscreen();
    grab();
    check_all("blend SRC_ALPHA,1-SRC_ALPHA", rgba_f(a, 0.0f, 1.0f - a, a * a + (1.0f - a)), TOL_Q);
    gl->Disable(GL_BLEND);

    /* A GLES 1.1-invalid source factor is rejected (GLES2 would accept it)
     * and the previous factors stay in effect. */
    gl->BlendFunc(GL_SRC_COLOR, GL_ONE);
    CHECK_EQ_INT(smgl_take_error(), GL_INVALID_ENUM);
    GLint src = 0;
    glGetIntegerv(GL_BLEND_SRC_RGB, &src);
    CHECK_EQ_INT(src, GL_SRC_ALPHA);
    return 0;
}

/* ---------------------------------------------------------------------- */
/* (i) depth test GL_LEQUAL with glDepthRangef.                             */

static void draw_colored_z(float r, float g, float b, float z)
{
    gl->Color4f(r, g, b, 1.0f);
    draw_quad_z(z);
}

static int scene_depth(void)
{
    CHECK(sm_host_egl_depth_bits() >= 16);
    gl->ClearDepthf(1.0f);
    clear(0, 0, 0, 1);
    gl->Enable(GL_DEPTH_TEST);
    gl->DepthFunc(GL_LEQUAL);
    gl->DepthMask(GL_TRUE);
    gl->DepthRangef(0.0f, 1.0f);

    /* Window depth = n + (f-n)(z_ndc+1)/2; with (0,1) and z = 0 -> 0.5. */
    draw_colored_z(1, 0, 0, 0.0f);   /* red, depth 0.5 */
    draw_colored_z(0, 1, 0, 0.0f);   /* green, equal depth: LEQUAL passes */
    draw_colored_z(0, 0, 1, 0.2f);   /* blue, 0.6: fails */
    grab();
    const rgba green = { 0, 255, 0, 255 };
    check_all("LEQUAL equal/greater", green, TOL_Q);

    /* The toon-outline range (-0.004, 0.996) is clamped to (0, 0.996):
     * z = 0.006 -> 0.996 * 1.006 / 2 = 0.500988 > 0.5: fails (unclamped it
     * would be 0.499 and pass). z = 0.002 -> 0.498996 < 0.5: passes. The
     * margins (~0.001) are ~65 steps of a 16-bit depth buffer. */
    gl->DepthRangef(-0.004f, 0.996f);
    draw_colored_z(1, 1, 0, 0.006f); /* yellow: must fail */
    grab();
    check_all("DepthRangef clamps near", green, TOL_Q);
    draw_colored_z(1, 0, 1, 0.002f); /* magenta: passes, writes 0.498996 */
    grab();
    const rgba magenta = { 255, 0, 255, 255 };
    check_all("DepthRangef offset passes", magenta, TOL_Q);

    /* Depth mask FALSE: white at depth 0.25 passes but does not write, so
     * cyan at 0.45 still passes against 0.498996. */
    gl->DepthRangef(0.0f, 1.0f);
    gl->DepthMask(GL_FALSE);
    draw_colored_z(1, 1, 1, -0.5f);
    gl->DepthMask(GL_TRUE);
    draw_colored_z(0, 1, 1, -0.1f);
    grab();
    const rgba cyan = { 0, 255, 255, 255 };
    check_all("DepthMask(FALSE)", cyan, TOL_Q);
    gl->Disable(GL_DEPTH_TEST);
    CHECK_EQ_INT(smgl_take_error(), 0);
    return 0;
}

/* ---------------------------------------------------------------------- */
/* state: validation errors, diagnostics, tracked-only state.               */

static int scene_state(void)
{
    sm_GLint v = -1;
    /* Invalid enums/values: recorded, command ignored. */
    gl->MatrixMode(0x1234);
    CHECK_EQ_INT(smgl_take_error(), GL_INVALID_ENUM);
    CHECK_EQ_INT(smgl_get_tracked(SMGL_GL_MATRIX_MODE, &v), 0);
    CHECK_EQ_INT(v, SMGL_GL_MODELVIEW);
    gl->Enable(0x1234);
    CHECK_EQ_INT(smgl_take_error(), GL_INVALID_ENUM);
    gl->EnableClientState(0x1234);
    CHECK_EQ_INT(smgl_take_error(), GL_INVALID_ENUM);
    gl->VertexPointer(5, GL_FLOAT, 0, NULL);
    CHECK_EQ_INT(smgl_take_error(), GL_INVALID_VALUE);
    gl->VertexPointer(3, GL_UNSIGNED_BYTE, 0, NULL);
    CHECK_EQ_INT(smgl_take_error(), GL_INVALID_ENUM);
    gl->TexCoordPointer(2, GL_FLOAT, -4, NULL);
    CHECK_EQ_INT(smgl_take_error(), GL_INVALID_VALUE);
    gl->DrawElements(GL_TRIANGLES, 3, GL_UNSIGNED_INT, NULL); /* ES3-only type */
    CHECK_EQ_INT(smgl_take_error(), GL_INVALID_ENUM);
    gl->BufferData(GL_ARRAY_BUFFER, 4, NULL, GL_STREAM_DRAW); /* not in GLES 1.1 */
    CHECK_EQ_INT(smgl_take_error(), GL_INVALID_ENUM);
    gl->Frustumf(-1, 1, -1, 1, 0.0f, 10.0f);
    CHECK_EQ_INT(smgl_take_error(), GL_INVALID_VALUE);
    gl->Orthof(1, 1, -1, 1, -1, 1);
    CHECK_EQ_INT(smgl_take_error(), GL_INVALID_VALUE);
    float m[16];
    CHECK_EQ_INT(smgl_get_matrix(SMGL_GL_MODELVIEW, m), 0);
    CHECK(m[0] == 1.0f && m[5] == 1.0f && m[10] == 1.0f && m[15] == 1.0f && m[14] == 0.0f);
    gl->PixelStorei(GL_UNPACK_ALIGNMENT, 3);
    CHECK_EQ_INT(smgl_take_error(), GL_INVALID_VALUE);
    gl->TexEnvf(SMGL_GL_TEXTURE_ENV, SMGL_GL_TEXTURE_ENV_MODE, 1234.0f);
    CHECK_EQ_INT(smgl_take_error(), GL_INVALID_ENUM);
    gl->ShadeModel(0);
    CHECK_EQ_INT(smgl_take_error(), GL_INVALID_ENUM);
    gl->Hint(0x1234, GL_FASTEST);
    CHECK_EQ_INT(smgl_take_error(), GL_INVALID_ENUM);
    static const unsigned char px4[4] = { 1, 2, 3, 4 };
    gl->TexImage2D(GL_TEXTURE_2D, 0, GL_RGB, 1, 1, 0, GL_RGBA, GL_UNSIGNED_BYTE, px4);
    CHECK_EQ_INT(smgl_take_error(), GL_INVALID_OPERATION);
    gl->TexImage2D(GL_TEXTURE_2D, 0, GL_RGBA, 1, 1, 1, GL_RGBA, GL_UNSIGNED_BYTE, px4);
    CHECK_EQ_INT(smgl_take_error(), GL_INVALID_VALUE);
    /* First error sticks until read. */
    gl->MatrixMode(0x1);
    gl->VertexPointer(9, GL_FLOAT, 0, NULL);
    CHECK_EQ_INT(smgl_take_error(), GL_INVALID_ENUM);
    CHECK_EQ_INT(smgl_take_error(), 0);
    CHECK(smgl_diag_count(SMGL_DIAG_GL_ERROR) >= 18);

    /* Tracked-only state (no GLES2 counterpart): no GLES2 call, no error. */
    gl->Enable(SMGL_GL_MULTISAMPLE);
    gl->Disable(SMGL_GL_MULTISAMPLE);
    CHECK_EQ_INT(smgl_get_tracked(SMGL_GL_MULTISAMPLE, &v), 0);
    CHECK_EQ_INT(v, 0);
    gl->Hint(SMGL_GL_PERSPECTIVE_CORRECTION_HINT, GL_FASTEST);
    gl->Hint(SMGL_GL_LINE_SMOOTH_HINT, GL_FASTEST);
    gl->Hint(SMGL_GL_FOG_HINT, GL_DONT_CARE);
    CHECK_EQ_INT(smgl_get_tracked(SMGL_GL_PERSPECTIVE_CORRECTION_HINT, &v), 0);
    CHECK_EQ_INT(v, GL_FASTEST);
    gl->ShadeModel(SMGL_GL_FLAT);
    CHECK_EQ_INT(smgl_get_tracked(SMGL_GL_SHADE_MODEL, &v), 0);
    CHECK_EQ_INT(v, SMGL_GL_FLAT);
    gl->Disable(SMGL_GL_ALPHA_TEST);
    CHECK_EQ_INT(smgl_take_error(), 0);
    CHECK_EQ_INT(glGetError(), GL_NO_ERROR);

    /* Flat shading, colour array enabled (defensive disable in the game),
     * normal array and alpha test: drawing still uses the current colour,
     * alpha func ALWAYS rejects nothing, and each is diagnosed. */
    clear(0, 0, 0, 0);
    gl->EnableClientState(SMGL_GL_COLOR_ARRAY);
    gl->EnableClientState(SMGL_GL_NORMAL_ARRAY);
    gl->Enable(SMGL_GL_ALPHA_TEST);
    gl->Color4f(0.2f, 0.4f, 0.6f, 0.0f);
    draw_fullscreen();
    grab();
    check_all("colour/normal arrays ignored, alpha test ALWAYS", rgba_f(0.2f, 0.4f, 0.6f, 0.0f), TOL_Q);
    CHECK_EQ_INT(smgl_diag_count(SMGL_DIAG_COLOR_ARRAY_DRAW), 1);
    CHECK_EQ_INT(smgl_diag_count(SMGL_DIAG_NORMAL_ARRAY_DRAW), 1);
    CHECK_EQ_INT(smgl_diag_count(SMGL_DIAG_ALPHA_TEST_DRAW), 1);
    gl->DisableClientState(SMGL_GL_COLOR_ARRAY);
    gl->DisableClientState(SMGL_GL_NORMAL_ARRAY);
    gl->Disable(SMGL_GL_ALPHA_TEST);

    /* Unsupported fixed-function state is diagnosed, never silently taken. */
    gl->Enable(SMGL_GL_LIGHTING);
    CHECK_EQ_INT(smgl_diag_count(SMGL_DIAG_UNSUPPORTED_CAP), 1);
    gl->Disable(SMGL_GL_LIGHTING);
    gl->TexEnvf(SMGL_GL_TEXTURE_ENV, SMGL_GL_TEXTURE_ENV_MODE, (float)SMGL_GL_COMBINE);
    CHECK_EQ_INT(smgl_diag_count(SMGL_DIAG_UNSUPPORTED_TEXENV), 1);
    CHECK_EQ_INT(smgl_get_tracked(SMGL_GL_TEXTURE_ENV_MODE, &v), 0);
    CHECK_EQ_INT(v, SMGL_GL_COMBINE);
    gl->TexEnvf(SMGL_GL_TEXTURE_ENV, SMGL_GL_TEXTURE_ENV_MODE, (float)SMGL_GL_MODULATE);

    /* NULL where GLES would dereference: diagnosed and ignored. */
    gl->MultMatrixf(NULL);
    CHECK_EQ_INT(smgl_diag_count(SMGL_DIAG_NULL_POINTER), 1);

    /* NPOT texture: diagnosed (GLES 1.1 core would reject it). */
    static const unsigned char rgb3[9] = { 0 };
    sm_GLuint tex = 0;
    gl->GenTextures(1, &tex);
    gl->BindTexture(GL_TEXTURE_2D, tex);
    gl->PixelStorei(GL_UNPACK_ALIGNMENT, 1);
    gl->TexImage2D(GL_TEXTURE_2D, 0, GL_RGB, 3, 1, 0, GL_RGB, GL_UNSIGNED_BYTE, rgb3);
    CHECK_EQ_INT(smgl_diag_count(SMGL_DIAG_TEX_NPOT), 1);
    gl->DeleteTextures(1, &tex);
    CHECK_EQ_INT(smgl_take_error(), 0);
    return 0;
}

/* ---------------------------------------------------------------------- */
/* png: sm_host_write_png writes top-down.                                  */

static int scene_png(const char *outdir)
{
    if (!sm_host_png_supported()) {
        fprintf(stderr, "built without libpng; skipping\n");
        return SKIP;
    }
    /* Bottom half red, top half green (GL y is bottom-up). */
    clear(0, 1, 0, 1);
    gl->Enable(GL_SCISSOR_TEST);
    gl->Scissor(0, 0, W, H / 2);
    clear(1, 0, 0, 1);
    gl->Disable(GL_SCISSOR_TEST);

    static unsigned char top_down[W * H * 4];
    CHECK_EQ_INT(sm_host_read_rgba_topdown(top_down), 0);
    CHECK(top_down[0] == 0 && top_down[1] == 255);                          /* top: green */
    CHECK(top_down[(H - 1) * W * 4] == 255 && top_down[(H - 1) * W * 4 + 1] == 0); /* bottom: red */

    char path[4096];
    snprintf(path, sizeof path, "%s/smgl_png_orientation.png", outdir);
    CHECK_EQ_INT(sm_host_write_png(path), 0);
#ifdef SMT_HAVE_PNG
    png_image img;
    memset(&img, 0, sizeof img);
    img.version = PNG_IMAGE_VERSION;
    CHECK(png_image_begin_read_from_file(&img, path) != 0);
    CHECK_EQ_INT(img.width, W);
    CHECK_EQ_INT(img.height, H);
    img.format = PNG_FORMAT_RGBA;
    static unsigned char back[W * H * 4];
    CHECK(png_image_finish_read(&img, NULL, back, 0, NULL) != 0);
    CHECK(memcmp(back, top_down, sizeof back) == 0);
    png_image_free(&img);
#endif
    return 0;
}

/* ---------------------------------------------------------------------- */

int main(int argc, char **argv)
{
    if (argc < 2) {
        fprintf(stderr, "usage: %s <scene> [outdir]\n", argv[0]);
        return 2;
    }
    const char *scene = argv[1];
    const char *outdir = (argc > 2) ? argv[2] : ".";
    if (strcmp(scene, "backend") == 0) {
        scene_backend();
        return smt_finish("rendering.backend");
    }
    static const struct { const char *name; int (*fn)(void); } scenes[] = {
        { "clear", scene_clear },       { "untextured", scene_untextured },
        { "ortho", scene_ortho },       { "vbo_short", scene_vbo_short },
        { "texture", scene_texture },   { "fog", scene_fog },
        { "pushpop", scene_pushpop },   { "blend", scene_blend },
        { "depth", scene_depth },       { "state", scene_state },
    };
    int (*fn)(void) = NULL;
    for (size_t i = 0; i < sizeof scenes / sizeof scenes[0]; ++i) {
        if (strcmp(scene, scenes[i].name) == 0) {
            fn = scenes[i].fn;
        }
    }
    if (fn == NULL && strcmp(scene, "png") != 0) {
        fprintf(stderr, "unknown scene '%s'\n", scene);
        return 2;
    }
    int rc = begin();
    if (rc != 0) {
        return rc;
    }
    rc = (fn != NULL) ? fn() : scene_png(outdir);
    end();
    if (rc == SKIP) {
        return SKIP;
    }
    char name[64];
    snprintf(name, sizeof name, "rendering.%s", scene);
    return smt_finish(name);
}
