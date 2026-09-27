/*
 * Display adaptation hooks (see port.h and tools/aot/hooks.txt).
 *
 * Adaptive fit: the original's 640x480 2D canvas is scaled uniformly by
 * s = min(W/640, H/480) and centred, instead of being stretched by W/640 and
 * H/480 independently. The mapping is applied where each 2D quad is placed
 * (G0RenderFont), where touch input enters (sm_port_map_touch), and to
 * full-screen images (backdrops, splash screens) through the 2D projection.
 * Quads that cover the whole canvas width or height (fades, full-screen
 * panels) keep the original full-screen extent on that axis.
 *
 * Adaptive FOV: see adapt_fovy().
 */
#include <math.h>
#include <string.h>

#include "aot_decls.h"
#include "aot_host.h"
#include "aot_platform.h"
#include "port.h"
#include "port_internal.h"

static sm_port_settings g_set = {SM_PORT_FIT_STRETCH, SM_PORT_FOV_ORIGINAL, SM_PORT_REFRESH_60};
static sm_port_listener g_listener;
static void *g_listener_user;

void sm_port_settings_original(sm_port_settings *s)
{
    s->fit = SM_PORT_FIT_STRETCH;
    s->fov = SM_PORT_FOV_ORIGINAL;
    s->refresh = SM_PORT_REFRESH_60;
}

void sm_port_settings_default(sm_port_settings *s)
{
    s->fit = SM_PORT_FIT_ADAPTIVE;
    s->fov = SM_PORT_FOV_ADAPTIVE;
    s->refresh = SM_PORT_REFRESH_60;
}

void sm_port_set(const sm_port_settings *s) { g_set = *s; }
void sm_port_get(sm_port_settings *s) { *s = g_set; }

static unsigned g_refresh_modes = 1u << SM_PORT_REFRESH_60;

void sm_port_set_refresh_modes(unsigned mask) { g_refresh_modes = mask | (1u << SM_PORT_REFRESH_60); }
unsigned sm_port_refresh_modes(void) { return g_refresh_modes; }

void sm_port_set_listener(sm_port_listener fn, void *user)
{
    g_listener = fn;
    g_listener_user = user;
}

void port_settings_changed(const sm_port_settings *s)
{
    g_set = *s;
    if (g_listener) {
        g_listener(&g_set, g_listener_user);
    }
}

/* ---------------------------------------------------------------- guest access */
uint32_t port_sym(const char *name)
{
    uint32_t a = aot_symbol_addr(name);
    if (!a) {
        aot_fatal("port: symbol %s not found in the original binary", name);
    }
    return a;
}

static inline float ldf(uint32_t a) { return aot_u2f(AOT_LD32(a)); }
static inline void stf(uint32_t a, float v) { AOT_ST32(a, aot_f2u(v)); }

/* Screen size as the game sees it (nativeResize: gG0ScreenWidth/Height). */
static int screen(float *w, float *h)
{
    static uint32_t aw, ah;
    if (!aw) {
        aw = port_sym("gG0ScreenWidth");
        ah = port_sym("gG0ScreenHeight");
    }
    *w = ldf(aw);
    *h = ldf(ah);
    return *w >= 1.0f && *h >= 1.0f;
}

/* Canvas mapping for the current screen: pixel = o + logical * s. Returns 0
 * when adaptation is off or would change nothing (a 4:3 screen). */
typedef struct canvas {
    float w, h;      /* screen */
    float sx, sy;    /* original per-axis scale (W/640, H/480) */
    float s, ox, oy; /* uniform scale and offsets */
} canvas;

static int canvas_get(canvas *cv)
{
    if (g_set.fit != SM_PORT_FIT_ADAPTIVE || !screen(&cv->w, &cv->h)) {
        return 0;
    }
    cv->sx = cv->w / 640.0f;
    cv->sy = cv->h / 480.0f;
    if (fabsf(cv->sx - cv->sy) < 1e-4f * cv->sy) {
        return 0;
    }
    cv->s = cv->sx < cv->sy ? cv->sx : cv->sy;
    cv->ox = (cv->w - 640.0f * cv->s) * 0.5f;
    cv->oy = (cv->h - 480.0f * cv->s) * 0.5f;
    return 1;
}

/* ---------------------------------------------------------------- 2D quads */
/* G0RenderFont(cRTexture*, x, y, x1, y1, x2, y2, x3, y3, w, h, u0, v0, z, v1,
 *              tColour&, blend, angle), v7a:0x7b7dc, softfp. Registers r1-r3
 * hold x, y, x1; y1..h are at incoming sp+0..sp+24 (prologue at 0x7b7dc:
 * 112-byte frame, vldr s24,[sp,#112] = y1 ... s14,[sp,#136] = h). With w == 0
 * the four corners are given explicitly; otherwise (x, y) is the top-left
 * corner and w, h the size (rotation, if any, is about the centre). */
#define ARG_X 1 /* x, y, x1 in r1-r3; y1 = arg 4 at sp+0 ... */
#define ARG_Y 2
#define ARG_W 9 /* sp+20 */
#define ARG_H 10 /* sp+24 */

static float qget(aot_cpu *c, int i)
{
    return i < 4 ? aot_u2f(c->r[i]) : ldf(c->r[13] + 4u * (uint32_t)(i - 4));
}
static void qset(aot_cpu *c, int i, float v)
{
    if (i < 4) {
        c->r[i] = aot_f2u(v);
    } else {
        stf(c->r[13] + 4u * (uint32_t)(i - 4), v);
    }
}

/* logical coordinate -> logical coordinate that the original's stretch maps to
 * pixel ox + x * s (pixel = original_scale * result). */
static float map_x(const canvas *cv, float ox, float x, int full) { return full ? x : (ox + x * cv->s) / cv->sx; }
static float map_y(const canvas *cv, float y, int full) { return full ? y : (cv->oy + y * cv->s) / cv->sy; }

/* Covers the whole canvas on that axis (fades, full-screen panels): keeps the
 * original full-screen extent. */
static int full_x(float x0, float x1) { return x0 <= 0.5f && x1 >= 639.5f; }
static int full_y(float y0, float y1) { return y0 <= 0.5f && y1 >= 479.5f; }

static void adapt_quad(aot_cpu *c, const canvas *cv, float ox)
{
    /* args 1..10: x y x1 y1 x2 y2 x3 y3 w h (arg n >= 4 is on the stack) */
    float w = qget(c, ARG_W), h = qget(c, ARG_H);
    if (w == 0.0f) {
        float px[4], py[4], x0 = 1e30f, x1 = -1e30f, y0 = 1e30f, y1 = -1e30f;
        int i, fx, fy;
        for (i = 0; i < 4; ++i) {
            px[i] = qget(c, ARG_X + 2 * i);
            py[i] = qget(c, ARG_Y + 2 * i);
            x0 = fminf(x0, px[i]); x1 = fmaxf(x1, px[i]);
            y0 = fminf(y0, py[i]); y1 = fmaxf(y1, py[i]);
        }
        fx = full_x(x0, x1);
        fy = full_y(y0, y1);
        for (i = 0; i < 4; ++i) {
            qset(c, ARG_X + 2 * i, map_x(cv, ox, px[i], fx));
            qset(c, ARG_Y + 2 * i, map_y(cv, py[i], fy));
        }
    } else {
        float x = qget(c, ARG_X), y = qget(c, ARG_Y);
        int fx = full_x(x, x + w);
        int fy = full_y(y, y + h);
        qset(c, ARG_X, map_x(cv, ox, x, fx));
        qset(c, ARG_Y, map_y(cv, y, fy));
        if (!fx) {
            qset(c, ARG_W, w * cv->s / cv->sx);
        }
        if (!fy) {
            qset(c, ARG_H, h * cv->s / cv->sy);
        }
    }
}

/* ---------------------------------------------------------------- smart HUD alignment */
/* In scenes (3D drawn behind the 2D layer: gameplay), each HUD element keeps
 * the screen edge it was designed against: an element in the left quarter of
 * the 640-wide canvas is placed at the same scaled distance from the left
 * screen edge, one in the right quarter from the right edge, anything else is
 * centred. Menus (no 3D scene) keep the whole layout together on the centred
 * canvas, aligned with their full-screen frame art.
 *
 * An "element" is found per frame from the original's own draw queue: every
 * 2D quad is a cFontPrintBuffer entry (0x84 bytes, FontPrintBuffer), queued by
 * FontPrint/OSDPrint during the last update of the frame and drawn by
 * FontPrintRender (v7a:0x22ce0) via FontPrintReal / OSDPrintReal. Entries
 * queued inside one cRBorder::Draw (v7a:0x4f5b0) belong to one widget; then
 * widgets and loose entries that lie mostly inside one another (a label in its
 * box, arrows on a slider) are merged. Each entry's extent is measured by
 * running FontPrintReal / OSDPrintReal with drawing suppressed (both only read
 * their entry and the font tables). */
enum { ANCHOR_CENTRE = 0, ANCHOR_LEFT = 1, ANCHOR_RIGHT = 2 };
#define FP_MAX 0x200
#define FP_SIZE 0x84u
#define EDGE_ZONE (640.0f / 4.0f)
#define TOUCH_RECTS 64

typedef struct rect {
    float x0, y0, x1, y1;
} rect;

typedef struct touch_rect {
    rect r;   /* screen pixels */
    float ox; /* canvas offset of its anchor */
} touch_rect;

static struct {
    uint32_t gen;                      /* frame counter (sm_port_frame_begin) */
    uint32_t next_group;
    uint32_t group[FP_MAX], group_gen[FP_MAX];
    uint8_t anchor[FP_MAX];
    uint32_t anchored_gen;             /* frame whose anchors are in anchor[] */
    int cur;                           /* entry being drawn, -1 outside */
    int measuring;
    rect m;
    int m_any;
} g_hud = {0, 1, {0}, {0}, {0}, 0xffffffffu, -1, 0, {0, 0, 0, 0}, 0};

static aot_lock g_touch_lock = AOT_LOCK_INIT;
static touch_rect g_touch[TOUCH_RECTS];
static int g_touch_n;

static float anchor_ox(const canvas *cv, int anchor)
{
    return anchor == ANCHOR_LEFT ? 0.0f : anchor == ANCHOR_RIGHT ? cv->w - 640.0f * cv->s : cv->ox;
}

static void rect_add(rect *r, int *any, float x0, float y0, float x1, float y1)
{
    if (!*any) {
        r->x0 = x0; r->y0 = y0; r->x1 = x1; r->y1 = y1;
        *any = 1;
        return;
    }
    r->x0 = fminf(r->x0, x0); r->y0 = fminf(r->y0, y0);
    r->x1 = fmaxf(r->x1, x1); r->y1 = fmaxf(r->y1, y1);
}

#define ARG_ANGLE 17 /* sp+52 */

static void measure_quad(aot_cpu *c)
{
    float w = qget(c, ARG_W), h = qget(c, ARG_H), x0, y0, x1, y1;
    if (w == 0.0f) {
        int i;
        x0 = y0 = 1e30f;
        x1 = y1 = -1e30f;
        for (i = 0; i < 4; ++i) {
            float px = qget(c, ARG_X + 2 * i), py = qget(c, ARG_Y + 2 * i);
            x0 = fminf(x0, px); x1 = fmaxf(x1, px);
            y0 = fminf(y0, py); y1 = fmaxf(y1, py);
        }
    } else {
        x0 = qget(c, ARG_X);
        y0 = qget(c, ARG_Y);
        x1 = x0 + w;
        y1 = y0 + h;
        if (qget(c, ARG_ANGLE) != 0.0f) { /* rotated about the centre */
            float cx = (x0 + x1) * 0.5f, cy = (y0 + y1) * 0.5f;
            float r = 0.5f * sqrtf(w * w + h * h);
            x0 = cx - r; x1 = cx + r; y0 = cy - r; y1 = cy + r;
        }
    }
    if (full_x(fminf(x0, x1), fmaxf(x0, x1))) {
        return; /* full-width quads (fades) stay full-screen and do not anchor */
    }
    rect_add(&g_hud.m, &g_hud.m_any, fminf(x0, x1), fminf(y0, y1), fmaxf(x0, x1), fmaxf(y0, y1));
}

static uint32_t fp_base(void)
{
    static uint32_t a;
    if (!a) {
        a = port_sym("FontPrintBuffer");
    }
    return a;
}
static uint32_t fp_count(void)
{
    static uint32_t a;
    uint32_t n;
    if (!a) {
        a = port_sym("FontPrintIndex");
    }
    n = AOT_LD32(a);
    return n > FP_MAX ? FP_MAX : n;
}

static int hud_active(canvas *cv) { return canvas_get(cv) && port_backdrop_fills_screen(); }

static float area(const rect *r) { return (r->x1 - r->x0) * (r->y1 - r->y0); }

/* Mostly inside one another: the overlap covers at least half of the smaller
 * one (a lone point or line counts when it lies inside the other). */
static int nested(const rect *a, const rect *b)
{
    float ix0 = fmaxf(a->x0, b->x0), iy0 = fmaxf(a->y0, b->y0);
    float ix1 = fminf(a->x1, b->x1), iy1 = fminf(a->y1, b->y1);
    float aa = area(a), ab = area(b), small;
    if (ix1 < ix0 || iy1 < iy0) {
        return 0;
    }
    small = fminf(aa, ab);
    if (small <= 0.0f) {
        return 1;
    }
    return (ix1 - ix0) * (iy1 - iy0) >= 0.5f * small;
}

static int uf_find(int *p, int i)
{
    while (p[i] != i) {
        p[i] = p[p[i]];
        i = p[i];
    }
    return i;
}

static void hud_layout(aot_cpu *c, const canvas *cv)
{
    static rect box[FP_MAX], item[FP_MAX];
    static int has[FP_MAX], item_of[FP_MAX], parent[FP_MAX], item_has[FP_MAX];
    static uint8_t item_anchor[FP_MAX];
    uint32_t base = fp_base(), n = fp_count(), i, j;
    uint32_t real = AOT_B + 0x22920u, osd = AOT_B + 0x22868u; /* FontPrintReal, OSDPrintReal */
    int nitems = 0, k, l, tn = 0;
    touch_rect tr[TOUCH_RECTS];

    /* 1. measure every queued entry */
    for (i = 0; i < n; ++i) {
        uint32_t e = base + i * FP_SIZE;
        g_hud.measuring = 1;
        g_hud.m_any = 0;
        aot_invoke(c, (AOT_LD32(e) & 1u) ? real : osd, &e, 1, NULL);
        g_hud.measuring = 0;
        box[i] = g_hud.m;
        has[i] = g_hud.m_any;
    }
    /* 2. items: one per border widget, one per loose entry */
    for (i = 0; i < n; ++i) {
        item_of[i] = -1;
        if (!has[i]) {
            continue;
        }
        if (g_hud.group_gen[i] == g_hud.gen && g_hud.group[i]) {
            for (j = 0; j < i; ++j) {
                if (item_of[j] >= 0 && g_hud.group_gen[j] == g_hud.gen && g_hud.group[j] == g_hud.group[i]) {
                    item_of[i] = item_of[j];
                    break;
                }
            }
        }
        if (item_of[i] < 0) {
            item_of[i] = nitems;
            item_has[nitems] = 0;
            ++nitems;
        }
        rect_add(&item[item_of[i]], &item_has[item_of[i]], box[i].x0, box[i].y0, box[i].x1, box[i].y1);
    }
    /* 3. merge items nested in one another */
    for (k = 0; k < nitems; ++k) {
        parent[k] = k;
    }
    for (k = 0; k < nitems; ++k) {
        for (l = k + 1; l < nitems; ++l) {
            if (nested(&item[k], &item[l])) {
                int a = uf_find(parent, k), b = uf_find(parent, l);
                if (a != b) {
                    parent[b] = a;
                }
            }
        }
    }
    for (k = 0; k < nitems; ++k) {
        int r = uf_find(parent, k);
        if (r != k) {
            rect_add(&item[r], &item_has[r], item[k].x0, item[k].y0, item[k].x1, item[k].y1);
        }
    }
    /* 4. anchor per merged element */
    for (k = 0; k < nitems; ++k) {
        const rect *r = &item[uf_find(parent, k)];
        item_anchor[k] = r->x1 <= EDGE_ZONE ? ANCHOR_LEFT : r->x0 >= 640.0f - EDGE_ZONE ? ANCHOR_RIGHT : ANCHOR_CENTRE;
        if (uf_find(parent, k) == k && item_anchor[k] != ANCHOR_CENTRE && tn < TOUCH_RECTS) {
            float ox = anchor_ox(cv, item_anchor[k]), pad = 12.0f * cv->s;
            tr[tn].ox = ox;
            tr[tn].r.x0 = ox + r->x0 * cv->s - pad;
            tr[tn].r.x1 = ox + r->x1 * cv->s + pad;
            tr[tn].r.y0 = cv->oy + r->y0 * cv->s - pad;
            tr[tn].r.y1 = cv->oy + r->y1 * cv->s + pad;
            ++tn;
        }
    }
    for (i = 0; i < n; ++i) {
        g_hud.anchor[i] = item_of[i] >= 0 ? item_anchor[item_of[i]] : ANCHOR_CENTRE;
    }
    g_hud.anchored_gen = g_hud.gen;
    aot_lock_acquire(&g_touch_lock);
    memcpy(g_touch, tr, sizeof(touch_rect) * (size_t)tn);
    g_touch_n = tn;
    aot_lock_release(&g_touch_lock);
}

void F_0004f5b0(aot_cpu *c) /* cRBorder::Draw: entries it queues form one widget */
{
    static uint32_t idx;
    uint32_t i0, i1, i, gid;
    if (!idx) {
        idx = port_sym("FontPrintIndex");
    }
    i0 = AOT_LD32(idx);
    F_0004f5b0_orig(c);
    i1 = AOT_LD32(idx);
    if (i1 > FP_MAX) {
        i1 = FP_MAX;
    }
    if (i1 > i0) {
        gid = g_hud.next_group++;
        if (!g_hud.next_group) {
            g_hud.next_group = 1;
        }
        for (i = i0; i < i1; ++i) {
            g_hud.group[i] = gid;
            g_hud.group_gen[i] = g_hud.gen;
        }
    }
}

void F_00022ce0(aot_cpu *c) /* FontPrintRender(int layer mask) */
{
    canvas cv;
    if (g_hud.anchored_gen != g_hud.gen && hud_active(&cv)) {
        hud_layout(c, &cv);
    }
    F_00022ce0_orig(c);
}

static void print_entry(aot_cpu *c, aot_fn orig)
{
    uint32_t e = c->r[0], base = fp_base();
    int prev = g_hud.cur;
    g_hud.cur = (e >= base && e < base + FP_MAX * FP_SIZE) ? (int)((e - base) / FP_SIZE) : -1;
    orig(c);
    g_hud.cur = prev;
}

void F_00022920(aot_cpu *c) { print_entry(c, F_00022920_orig); } /* FontPrintReal */
void F_00022868(aot_cpu *c) { print_entry(c, F_00022868_orig); } /* OSDPrintReal */

void F_0007b7dc(aot_cpu *c) /* G0RenderFont */
{
    canvas cv;
    if (g_hud.measuring) {
        measure_quad(c);
        return;
    }
    if (canvas_get(&cv)) {
        float ox = cv.ox;
        if (g_hud.cur >= 0 && g_hud.anchored_gen == g_hud.gen) {
            ox = anchor_ox(&cv, g_hud.anchor[g_hud.cur]);
        }
        adapt_quad(c, &cv, ox);
    }
    F_0007b7dc_orig(c);
}

/* ---------------------------------------------------------------- full-screen images */
/* While set, the 2D projection (glOrthof(0, W, H, 0, -1, 1) issued by
 * G0FontRenderStart / cRSplashManager::RenderStart) is narrowed so that the
 * image's 0..W x 0..H pixels land on the canvas rectangle. */
static int g_canvas_proj;
static float g_color_mul = 1.0f;

static int g_persp;                  /* current projection is a 3D camera */
static unsigned g_3d_draws, g_3d_draws_prev; /* draws under a 3D camera: this frame, last frame */

static void filter_ortho(float v[6])
{
    canvas cv;
    g_persp = 0;
    if (!g_canvas_proj || !canvas_get(&cv)) {
        return;
    }
    if (v[0] != 0.0f || v[3] != 0.0f || fabsf(v[1] - cv.w) > 0.5f || fabsf(v[2] - cv.h) > 0.5f) {
        return; /* not the screen-pixel projection */
    }
    {
        float kx = 640.0f * cv.s / cv.w, ky = 480.0f * cv.s / cv.h;
        float l = -cv.ox / kx, t = -cv.oy / ky;
        v[0] = l;
        v[1] = l + cv.w / kx;
        v[3] = t;
        v[2] = t + cv.h / ky;
    }
}

static void filter_color(float v[4])
{
    v[0] *= g_color_mul;
    v[1] *= g_color_mul;
    v[2] *= g_color_mul;
}

static void filter_draw(void)
{
    if (g_persp) {
        ++g_3d_draws;
    }
}

static void install_filters(void)
{
    aot_gl_port_filter.ortho = filter_ortho;
    aot_gl_port_filter.color = filter_color;
    aot_gl_port_filter.draw = filter_draw;
}

/* G0SetColour skips glColor4f when the colour equals its cache GLColour
 * (v7a:0x7acfc); invalidate the cache so a pass with a different multiplier
 * re-issues the colour. 0x00000000 is transparent black, which the image
 * passes below never use. */
static void invalidate_colour_cache(void)
{
    static uint32_t a;
    if (!a) {
        a = port_sym("GLColour");
    }
    AOT_ST32(a, 0u);
}

/* Draw a full-screen image twice: stretched over the whole screen and dimmed
 * (fills the margins beside the canvas), then aspect-correct on the canvas. */
static void image_two_pass(aot_cpu *c, aot_fn orig, float dim)
{
    aot_cpu entry = *c;
    uint32_t sp = c->r[13], args[8];
    int i;
    for (i = 0; i < 8; ++i) {
        args[i] = AOT_LD32(sp + 4u * (uint32_t)i); /* stack args may be rewritten by the callee */
    }
    g_color_mul = dim;
    invalidate_colour_cache();
    orig(c);
    g_color_mul = 1.0f;
    invalidate_colour_cache();
    *c = entry;
    for (i = 0; i < 8; ++i) {
        AOT_ST32(sp + 4u * (uint32_t)i, args[i]);
    }
    g_canvas_proj = 1;
    orig(c);
    g_canvas_proj = 0;
}

/* G0RenderBackdrop(cGLVertexUV*, vbo, count, cRTexture*), v7a:0x7c8b8:
 * the scrolling full-screen backdrop, drawn from a grid of screen-pixel
 * vertices built by cRBackdrop::Render. */
void F_0007c8b8(aot_cpu *c)
{
    canvas cv;
    if (!canvas_get(&cv) || port_backdrop_fills_screen()) {
        F_0007c8b8_orig(c);
        return;
    }
    image_two_pass(c, F_0007c8b8_orig, 0.45f);
}

/* cRSplashManager::Render(float), v7a:0x79cf8: splash and loading screens,
 * a full-screen image plus the loading bar in screen pixels. It clears the
 * screen itself, so the canvas pass alone gives black margins. */
void F_00079cf8(aot_cpu *c)
{
    canvas cv;
    if (!canvas_get(&cv)) {
        F_00079cf8_orig(c);
        return;
    }
    g_canvas_proj = 1;
    F_00079cf8_orig(c);
    g_canvas_proj = 0;
}

/* ---------------------------------------------------------------- field of view */
/* gluPerspective(fovy, aspect, near, far, yshift), v7a:0x7b0c8, softfp: the
 * game passes its vertical FOV and the real viewport aspect, so on wide
 * screens the horizontal FOV grows (tan(h/2) = tan(v/2) * aspect). Gameplay
 * uses fovy 100-120 degrees, i.e. 150 degrees horizontally at 19.5:9.
 *
 * Adaptive: the horizontal FOV is limited to what the same camera shows on a
 * 16:9 screen (854x480, the widest Android screens of the game's time), but
 * only for cameras wider than ADAPT_MIN_H at that aspect; narrow cameras keep
 * the original behaviour, which keeps 3D elements aligned with the 2D canvas. */
#define ADAPT_ASPECT (16.0f / 9.0f)
#define ADAPT_MIN_H_DEG 90.0f

static float adapt_fovy(float fovy_deg, float aspect)
{
    const float d2r = 3.14159265f / 180.0f;
    float tv = tanf(fovy_deg * 0.5f * d2r);
    float th_ref = tv * ADAPT_ASPECT;
    float th_cap;
    if (aspect <= ADAPT_ASPECT || fovy_deg <= 0.0f || fovy_deg >= 179.0f) {
        return fovy_deg;
    }
    th_cap = fmaxf(th_ref, tanf(ADAPT_MIN_H_DEG * 0.5f * d2r));
    if (tv * aspect <= th_cap) {
        return fovy_deg;
    }
    return 2.0f * atanf(th_cap / aspect) / d2r;
}

void F_0007b0c8(aot_cpu *c) /* gluPerspective */
{
    g_persp = 1;
    if (g_set.fov == SM_PORT_FOV_ADAPTIVE) {
        c->r[0] = aot_f2u(adapt_fovy(aot_u2f(c->r[0]), aot_u2f(c->r[1])));
    }
    F_0007b0c8_orig(c);
}

/* ---------------------------------------------------------------- touch, frames */
void sm_port_map_touch(float *x, float *y)
{
    canvas cv;
    float ox;
    int i;
    if (!canvas_get(&cv)) {
        return;
    }
    ox = cv.ox;
    aot_lock_acquire(&g_touch_lock);
    for (i = 0; i < g_touch_n; ++i) {
        const rect *r = &g_touch[i].r;
        if (*x >= r->x0 && *x <= r->x1 && *y >= r->y0 && *y <= r->y1) {
            ox = g_touch[i].ox;
            break;
        }
    }
    aot_lock_release(&g_touch_lock);
    /* screen pixel -> logical (inverse of the element's placement) -> the
     * pixel that the original's x * 640/W scaling turns into that logical x */
    *x = (*x - ox) / cv.s * cv.sx;
    *y = (*y - cv.oy) / cv.s * cv.sy;
}

void sm_port_frame_begin(void)
{
    install_filters();
    if (g_hud.anchored_gen != g_hud.gen) {
        /* last frame had no anchored elements: touches use the centred canvas */
        aot_lock_acquire(&g_touch_lock);
        g_touch_n = 0;
        aot_lock_release(&g_touch_lock);
    }
    ++g_hud.gen;
    g_3d_draws_prev = g_3d_draws;
    g_3d_draws = 0;
}

/* Menus set up their 3D cameras but draw nothing with them; gameplay (and the
 * intro) draws its scene over the backdrop. Measured per frame with the GL
 * trace (docs/PORTING_STATUS.md, display adaptation). */
int port_backdrop_fills_screen(void) { return g_3d_draws_prev > 0; }
