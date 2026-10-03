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
#include <stdlib.h>

#include "aot_decls.h"
#include "aot_host.h"
#include "aot_platform.h"
#include "port.h"
#include "port_internal.h"

static sm_port_settings g_set = {SM_PORT_FIT_STRETCH, SM_PORT_FOV_ORIGINAL, SM_PORT_REFRESH_60,
                                 SM_PORT_CONTROLS_LEGACY, 50};
static sm_port_listener g_listener;
static int g_background_only;
static void *g_listener_user;

void sm_port_settings_original(sm_port_settings *s)
{
    s->fit = SM_PORT_FIT_STRETCH;
    s->fov = SM_PORT_FOV_ORIGINAL;
    s->refresh = SM_PORT_REFRESH_60;
    s->controls = SM_PORT_CONTROLS_LEGACY;
    s->smoothness = 50;
}

void sm_port_settings_default(sm_port_settings *s)
{
    s->fit = SM_PORT_FIT_ADAPTIVE;
    s->fov = SM_PORT_FOV_ADAPTIVE;
    s->refresh = SM_PORT_REFRESH_60;
    s->controls = SM_PORT_CONTROLS_LEGACY;
    s->smoothness = 50;
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
    float tx, ty; /* legacy logical coordinates -> pixels */
    int nameentry;
} canvas;

static int canvas_get(canvas *cv)
{
    cv->nameentry = port_nameentry_active();
    if ((!cv->nameentry && g_set.fit != SM_PORT_FIT_ADAPTIVE) || !screen(&cv->w, &cv->h)) {
        return 0;
    }
    cv->sx = cv->w / 640.0f;
    cv->sy = cv->h / 480.0f;
    if (cv->nameentry) {
        /* Legacy coordinates are normalized into the actual 480x320 source
         * art canvas. Only one uniform source-to-display scale is applied. */
        sm_name_canvas nc = port_nameentry_canvas(cv->w, cv->h);
        cv->s = nc.scale; cv->ox = nc.x; cv->oy = nc.y;
        cv->tx = nc.scale * (480.0f / 640.0f);
        cv->ty = nc.scale * (320.0f / 480.0f);
        return 1;
    }
    if (fabsf(cv->sx - cv->sy) < 1e-4f * cv->sy) {
        return 0;
    }
    cv->s = cv->sx < cv->sy ? cv->sx : cv->sy;
    cv->tx = cv->ty = cv->s;
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
static float map_x(const canvas *cv, float ox, float x, int full) { return full && !cv->nameentry ? x : (ox + x * cv->tx) / cv->sx; }
static float map_y(const canvas *cv, float y, int full) { return full && !cv->nameentry ? y : (cv->oy + y * cv->ty) / cv->sy; }

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
        if (!fx || cv->nameentry) {
            qset(c, ARG_W, w * cv->tx / cv->sx);
        }
        if (!fy || cv->nameentry) {
            qset(c, ARG_H, h * cv->ty / cv->sy);
        }
    }
}

/* ---------------------------------------------------------------- smart HUD alignment */
/* In scenes (3D drawn behind the 2D layer: gameplay), each HUD element keeps
 * the screen edge it was designed against: an element in the left quarter of
 * the 640-wide canvas is placed at the same scaled distance from the left
 * screen edge, one in the right quarter from the right edge, anything else is
 * centred. Menus keep interactive controls on the centred canvas; their
 * lower-right decorative leaf follows the widened frame's right margin.
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
    float ox, oy; /* the same origin used to draw this widget */
} touch_rect;

static struct {
    uint32_t gen;                      /* frame counter (sm_port_frame_begin) */
    uint32_t next_group;
    uint32_t group[FP_MAX], group_gen[FP_MAX];
    uint8_t anchor[FP_MAX];
    float offset[FP_MAX], offset_y[FP_MAX];
    uint32_t anchored_gen;             /* frame whose anchors are in anchor[] */
    int cur;                           /* entry being drawn, -1 outside */
    int measuring;
    rect m;
    int m_any;
} g_hud = {.next_group = 1, .anchored_gen = 0xffffffffu, .cur = -1};

static aot_lock g_touch_lock = AOT_LOCK_INIT;
static touch_rect g_touch[TOUCH_RECTS];
static int g_touch_n;
static float g_ui_snapshot[FP_MAX * 9];
static int g_ui_snapshot_count;
int sm_port_ui_debug(float *out, int capacity)
{
    int n = capacity < g_ui_snapshot_count ? capacity : g_ui_snapshot_count;
    memcpy(out,g_ui_snapshot,sizeof(float)*(size_t)n);
    return n;
}

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

static int g_menu_backdrop, g_galaxy_backdrop;
static float g_galaxy_detail_dx, g_galaxy_detail_dy;
static rect g_galaxy_detail_source;
static int hud_active(canvas *cv) { return canvas_get(cv) && !cv->nameentry && (port_backdrop_fills_screen() || g_menu_backdrop || g_galaxy_backdrop); }

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
            /* Header/footer chrome remains independent when an original popup
             * overlaps its legacy coordinates before adaptive placement. */
            if (g_galaxy_backdrop &&
                    ((item[k].y1<=42 || item[k].y0>=410) !=
                     (item[l].y1<=42 || item[l].y0>=410))) continue;
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
    /* Galaxy chrome uses screen edges. The original map coordinates remain
     * centred; details and the complete navigation row get their own origins. */
    float item_offset[FP_MAX], item_y[FP_MAX], nav_x0=1e30f,nav_x1=-1e30f;
    if(g_galaxy_backdrop) for(k=0;k<nitems;++k) {
        const rect *r=&item[uf_find(parent,k)];
        if(r->y0>=400 && r->x0>=160) {
            nav_x0=fminf(nav_x0,r->x0);nav_x1=fmaxf(nav_x1,r->x1);
        }
    }
    /* 4. anchor per merged element */
    g_ui_snapshot_count=0;
    for (k = 0; k < nitems; ++k) {
        const rect *r = &item[uf_find(parent, k)];
        if (g_galaxy_backdrop) {
            float cx=(r->x0+r->x1)*.5f;
            item_anchor[k]=r->y1<=70 ? (cx<320 ? ANCHOR_LEFT : ANCHOR_RIGHT)
                : r->y0>=400 && r->x1<=160 ? ANCHOR_LEFT : ANCHOR_CENTRE;
        } else if (g_menu_backdrop) {
            /* The menu's small lower-right leaf is a queued widget rather
             * than part of the backdrop. Keep its original size and margin
             * while the other menu controls remain centred in the panel. */
            item_anchor[k] = r->x0 >= 640.0f - EDGE_ZONE && r->y0 >= 380.0f
                           ? ANCHOR_RIGHT : ANCHOR_CENTRE;
        } else {
            item_anchor[k] = r->x1 <= EDGE_ZONE ? ANCHOR_LEFT : r->x0 >= 640.0f - EDGE_ZONE ? ANCHOR_RIGHT : ANCHOR_CENTRE;
        }
        item_offset[k]=anchor_ox(cv,item_anchor[k]);
        item_y[k]=cv->oy;
        if(g_galaxy_backdrop) {
            if(r->y0>=400 && r->x0>=160 && nav_x1>=nav_x0)
                item_offset[k]=cv->ox+(320-(nav_x0+nav_x1)*.5f)*cv->s;
            else if(r->x1-r->x0>250 && r->y1-r->y0>100)
            {
                g_galaxy_detail_dx=320-(r->x0+r->x1)*.5f;
                g_galaxy_detail_dy=240-(r->y0+r->y1)*.5f;
                g_galaxy_detail_source=*r;
                item_offset[k]=cv->ox+g_galaxy_detail_dx*cv->s;
                item_y[k]=cv->oy+g_galaxy_detail_dy*cv->s;
            }
        }
        if (uf_find(parent, k) == k && (fabsf(item_offset[k]-cv->ox)>.01f || fabsf(item_y[k]-cv->oy)>.01f) && tn < TOUCH_RECTS) {
            float ox = item_offset[k], pad = 12.0f * cv->s;
            tr[tn].ox = ox;
            tr[tn].oy = item_y[k];
            tr[tn].r.x0 = ox + r->x0 * cv->s - pad;
            tr[tn].r.x1 = ox + r->x1 * cv->s + pad;
            tr[tn].r.y0 = item_y[k] + r->y0 * cv->s - pad;
            tr[tn].r.y1 = item_y[k] + r->y1 * cv->s + pad;
            ++tn;
        }
        if(uf_find(parent,k)==k && g_ui_snapshot_count+9<=FP_MAX*9) {
            float *p=g_ui_snapshot+g_ui_snapshot_count;
            p[0]=g_galaxy_backdrop && r->x1-r->x0>250 && r->y1-r->y0>100 ? 1 : 0;
            p[1]=r->x0;p[2]=r->y0;p[3]=r->x1;p[4]=r->y1;
            p[5]=item_offset[k]+r->x0*cv->s;p[6]=item_y[k]+r->y0*cv->s;
            p[7]=item_offset[k]+r->x1*cv->s;p[8]=item_y[k]+r->y1*cv->s;
            g_ui_snapshot_count+=9;
        }
    }
    for (i = 0; i < n; ++i) {
        g_hud.anchor[i] = item_of[i] >= 0 ? item_anchor[item_of[i]] : ANCHOR_CENTRE;
        g_hud.offset[i] = item_of[i] >= 0 ? item_offset[item_of[i]] : cv->ox;
        g_hud.offset_y[i] = item_of[i] >= 0 ? item_y[item_of[i]] : cv->oy;
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
    if (port_nameentry_hide_border(c->r[0])) return;
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

void F_000500ac(aot_cpu *c)
{
    if (port_nameentry_hide_border(c->r[0])) return;
    F_000500ac_orig(c);
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
    if (g_background_only) return;
    canvas cv;
    if (g_hud.measuring) {
        /* Map nodes and route lines can sit beneath a translucent panel.
         * They are independent map geometry, never children of that panel. */
        const char *measured_texture=aot_host(c->r[0]+12u);
        if(g_galaxy_backdrop && (!strncmp(measured_texture,"Galaxy/Line",11) ||
                !strncmp(measured_texture,"Galaxy/Level",12) ||
                !strcmp(measured_texture,"Galaxy/SpaceMapLogo.tga"))) return;
        measure_quad(c);
        return;
    }
    if (canvas_get(&cv)) {
        float ox = cv.ox;
        if (!cv.nameentry && g_hud.cur >= 0 && g_hud.anchored_gen == g_hud.gen) {
            ox = g_hud.offset[g_hud.cur];
            cv.oy = g_hud.offset_y[g_hud.cur];
        }
        if(g_galaxy_backdrop) {
            const char *texture_name=aot_host(c->r[0]+12u);
            if(!strcmp(texture_name,"Galaxy/SpaceMapLogo.tga")) ox=cv.w-640*cv.s;
            if((g_galaxy_detail_dx || g_galaxy_detail_dy) &&
                    (!strcmp(texture_name,"Galaxy/Line.tga") || !strcmp(texture_name,"Galaxy/Linepro.tga"))) {
                float width=qget(c,ARG_W),height=qget(c,ARG_H);
                if(width>0 && fabsf(height)<=4) {
                    float x=qget(c,ARG_X),y=qget(c,ARG_Y);
                    qset(c,ARG_W,0);
                    qset(c,ARG_X,x);qset(c,ARG_Y,y);
                    qset(c,ARG_X+2,x+width);qset(c,ARG_Y+2,y);
                    qset(c,ARG_X+4,x+width);qset(c,ARG_Y+4,y+height);
                    qset(c,ARG_X+6,x);qset(c,ARG_Y+6,y+height);
                    width=0;
                }
                if(width==0) {
                    float x0=1e30f,x1=-1e30f,y0=1e30f,y1=-1e30f;
                    for(int i=0;i<4;++i){float x=qget(c,ARG_X+2*i),y=qget(c,ARG_Y+2*i);x0=fminf(x0,x);x1=fmaxf(x1,x);y0=fminf(y0,y);y1=fmaxf(y1,y);}
                    float d0=fminf(fabsf(x0-g_galaxy_detail_source.x0),fabsf(x0-g_galaxy_detail_source.x1));
                    float d1=fminf(fabsf(x1-g_galaxy_detail_source.x0),fabsf(x1-g_galaxy_detail_source.x1));
                    if(y1-y0<=4 && x1-x0>1)for(int i=0;i<4;++i) {
                        float x=qget(c,ARG_X+2*i);
                        if((x<(x0+x1)*.5f)==(d0<d1)) {
                            qset(c,ARG_X+2*i,x+g_galaxy_detail_dx);
                            qset(c,ARG_Y+2*i,qget(c,ARG_Y+2*i)+g_galaxy_detail_dy);
                        }
                    }
                }
            }
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
static int g_splash_proj, g_splash_first_draw, g_splash_slot = -1;
static float g_splash_w, g_splash_h;
enum { ART_SANDLOT, ART_ALPHA72, ART_MENU_FRAME, ART_BACKGROUND,
       ART_MENU_ORIGINAL, ART_LOADING, ART_LOADING_ORIGINAL, ART_STAR_EXTENDED, ART_COUNT };
static struct { int w, h; unsigned char *pixels; unsigned texture; } g_splash_art[ART_COUNT];
static int g_menu_layer, g_space_layer;
static void space_layer_draw(void);
static unsigned art_texture(int slot);
static void menu_layer_draw(void);
static void splash_first_draw(void);
static float g_color_mul = 1.0f;

static int g_persp;                  /* current projection is a 3D camera */
static unsigned g_3d_draws, g_3d_draws_prev; /* draws under a 3D camera: this frame, last frame */

static void filter_ortho(float v[6])
{
    canvas cv;
    g_persp = 0;
    if ((!g_canvas_proj && !g_splash_proj) || !canvas_get(&cv)) {
        return;
    }
    if (g_splash_proj) {
        float s = fminf(cv.w / 480.0f, cv.h / 320.0f);
        cv.ox = (cv.w - 480.0f*s)*0.5f; cv.oy = (cv.h - 320.0f*s)*0.5f;
        cv.tx = s*480.0f/640.0f; cv.ty = s*320.0f/480.0f;
    }
    if (v[0] != 0.0f || v[3] != 0.0f || fabsf(v[1] - cv.w) > 0.5f || fabsf(v[2] - cv.h) > 0.5f) {
        return; /* not the screen-pixel projection */
    }
    {
        float kx = 640.0f * cv.tx / cv.w, ky = 480.0f * cv.ty / cv.h;
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
    if (g_splash_first_draw) { g_splash_first_draw = 0; splash_first_draw(); }
    if (g_menu_layer) menu_layer_draw();
    if (g_space_layer) { g_space_layer=0; space_layer_draw(); }
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

/* MenuScreenHoriz is one 512x512 texture whose landscape backdrop samples
 * 480x320 art pixels. Split beside the left ornament, preserving both outer
 * compositions and widening the panel between them. The original open gap
 * between the left green tube and logo surround stays open. */
enum { MENU_GRID = 21, MENU_VERTS = MENU_GRID * MENU_GRID,
       MENU_TEX_W = 512, MENU_VISIBLE_W = 480, MENU_VISIBLE_H = 320 };
typedef struct menu_vertex { float x, y, z, u, v; } menu_vertex;

void sm_port_splash_asset(int slot, int w, int h, const unsigned char *rgba)
{
    if (slot < 0 || slot >= ART_COUNT || w < 1 || h < 1 || w > 4096 || h > 4096 || !rgba) return;
    int original = slot == ART_MENU_FRAME ? ART_MENU_ORIGINAL : slot == ART_LOADING ? ART_LOADING_ORIGINAL : -1;
    if (original >= 0 && g_splash_art[original].pixels) {
        /* Use generated artwork only as a matte. Exact original RGB pixels
         * preserve the frame, logo lettering and loading-bar graphics. */
        int ow = g_splash_art[original].w, oh = g_splash_art[original].h;
        unsigned char *composite = malloc((size_t)ow*oh*4);
        if (!composite) return;
        for (int y=0;y<oh;++y) for (int x=0;x<ow;++x) {
            size_t p = ((size_t)y*ow+x)*4;
            size_t m = ((size_t)(y*h/oh)*w+x*w/ow)*4;
            memcpy(composite+p, g_splash_art[original].pixels+p, 3);
            composite[p+3] = rgba[m+3] < 16 ? 0 : rgba[m+3];
        }
        free(g_splash_art[slot].pixels);
        g_splash_art[slot].pixels = composite; g_splash_art[slot].w=ow; g_splash_art[slot].h=oh;
        return;
    }
    size_t n = (size_t)w*h*4;
    unsigned char *copy = malloc(n);
    if (!copy) return;
    memcpy(copy, rgba, n);
    free(g_splash_art[slot].pixels);
    g_splash_art[slot].pixels = copy; g_splash_art[slot].w = w; g_splash_art[slot].h = h;
}

void sm_port_splash_context_created(void)
{
    /* The old IDs belong to the destroyed GL context. Retain CPU pixels for
     * re-upload; never delete stale IDs in the newly created context. */
    for (int i=0;i<ART_COUNT;++i) g_splash_art[i].texture = 0;
}

void sm_port_background_debug_only(int on) { g_background_only = on; }

static unsigned art_texture(int slot)
{
    const sm_gl_backend *gl = aot_cfg->gl;
    if (!g_splash_art[slot].pixels) return 0;
    if (!g_splash_art[slot].texture) {
        gl->GenTextures(1, &g_splash_art[slot].texture);
        gl->BindTexture(0x0de1, g_splash_art[slot].texture);
        gl->TexParameteri(0x0de1,0x2801,0x2601); gl->TexParameteri(0x0de1,0x2800,0x2601);
        gl->TexParameteri(0x0de1,0x2802,0x812f); gl->TexParameteri(0x0de1,0x2803,0x812f);
        gl->TexImage2D(0x0de1,0,0x1908,g_splash_art[slot].w,g_splash_art[slot].h,0,0x1908,0x1401,g_splash_art[slot].pixels);
    }
    return g_splash_art[slot].texture;
}

static void art_cover(int slot, float w, float h)
{
    const sm_gl_backend *gl = aot_cfg->gl;
    unsigned tex = art_texture(slot);
    if (!tex) return;
    float s = fmaxf(w/g_splash_art[slot].w,h/g_splash_art[slot].h);
    float u = (1-w/(s*g_splash_art[slot].w))*.5f;
    float v = (1-h/(s*g_splash_art[slot].h))*.5f;
    menu_vertex q[4]={{0,0,0,u,v},{w,0,0,1-u,v},{w,h,0,1-u,1-v},{0,h,0,u,1-v}};
    const unsigned short indices[4]={0,1,3,2};
    gl->BindTexture(0x0de1,tex); gl->VertexPointer(3,0x1406,sizeof *q,q);
    gl->TexCoordPointer(2,0x1406,sizeof *q,&q[0].u);
    gl->DrawElements(5,4,0x1403,indices);
}

static menu_vertex *g_menu_vertices;
static unsigned g_menu_index_buffer;
static float g_menu_w, g_menu_h;
static void space_layer_draw(void)
{
    const sm_gl_backend *gl=aot_cfg->gl;
    gl->BindTexture(0x0de1,art_texture(ART_STAR_EXTENDED));
    gl->VertexPointer(3,0x1406,sizeof *g_menu_vertices,g_menu_vertices);
    gl->TexCoordPointer(2,0x1406,sizeof *g_menu_vertices,&g_menu_vertices[0].u);
}

static void menu_layer_draw(void)
{
    const sm_gl_backend *gl = aot_cfg->gl;
    if (g_menu_layer == 1) {
        /* Original backdrop indices live in an element VBO. Our background
         * quad has client indices; unbind and restore the original VBO. */
        gl->BindBuffer(0x8893,0);
        art_cover(ART_BACKGROUND,g_menu_w,g_menu_h);
        gl->BindBuffer(0x8893,g_menu_index_buffer);
    }
    gl->BindTexture(0x0de1,art_texture(ART_MENU_FRAME));
    gl->VertexPointer(3,0x1406,sizeof *g_menu_vertices,g_menu_vertices);
    gl->TexCoordPointer(2,0x1406,sizeof *g_menu_vertices,&g_menu_vertices[0].u);
    gl->Enable(0x0be2); gl->BlendFunc(0x0302,0x0303);
    if (g_background_only) gl->Color4f(1,1,1,0);
}

static void splash_first_draw(void)
{
    const sm_gl_backend *gl = aot_cfg->gl;
    menu_vertex *q = aot_host(port_sym("gSplashSpriteVertexUVArray"));
    if (g_splash_slot >= 0 && g_splash_art[g_splash_slot].pixels) {
        int slot = g_splash_slot;
        gl->BindTexture(0x0de1, art_texture(slot));
        /* Uniform cover: crop only the extended outer artwork, never stretch
         * a logo. Bitmap rows arrive top-first, so top uses V=0. */
        float s = fmaxf(g_splash_w/g_splash_art[slot].w, g_splash_h/g_splash_art[slot].h);
        float u = (1.0f-g_splash_w/(s*g_splash_art[slot].w))*0.5f;
        float v = (1.0f-g_splash_h/(s*g_splash_art[slot].h))*0.5f;
        q[0] = (menu_vertex){0,0,0,u,v}; q[1] = (menu_vertex){g_splash_w,0,0,1-u,v};
        q[2] = (menu_vertex){g_splash_w,g_splash_h,0,1-u,1-v}; q[3] = (menu_vertex){0,g_splash_h,0,u,1-v};
    } else if (g_splash_slot == -1 && g_splash_art[ART_BACKGROUND].pixels && g_splash_art[ART_LOADING].pixels) {
        /* Temporarily use a full-screen projection for the background, then
         * restore the uniform loading canvas for both its image and bar. */
        gl->MatrixMode(0x1701); gl->PushMatrix(); gl->LoadIdentity();
        gl->Orthof(0,g_splash_w,g_splash_h,0,-1,1);
        art_cover(ART_BACKGROUND,g_splash_w,g_splash_h);
        gl->PopMatrix(); gl->MatrixMode(0x1700);
        gl->BindTexture(0x0de1,art_texture(ART_LOADING));
        for (int i=0;i<4;++i) q[i].v = 1.0f-q[i].v;
        gl->VertexPointer(3,0x1406,sizeof *q,q); gl->TexCoordPointer(2,0x1406,sizeof *q,&q[0].u);
        gl->Enable(0x0be2); gl->BlendFunc(0x0302,0x0303);
    } else if (g_splash_proj && g_splash_w/g_splash_h >= 16.0f/9.0f) {
        /* Continue background edge colours into the side areas. The original
         * loading logo, bar frame and progress fill stay on one 480x320 canvas. */
        float extent = (g_splash_w-1.5f*g_splash_h)*g_splash_w/(3.0f*g_splash_h);
        const unsigned short indices[4] = {0,1,3,2};
        menu_vertex edge[4];
        for (int side=0;side<2;++side) {
            float x0 = side ? g_splash_w : -extent, x1 = side ? g_splash_w+extent : 0;
            float u = side ? 479.5f/512.0f : 0.5f/512.0f;
            edge[0]=(menu_vertex){x0,0,0,u,1}; edge[1]=(menu_vertex){x1,0,0,u,1};
            edge[2]=(menu_vertex){x1,g_splash_h,0,u,.375f}; edge[3]=(menu_vertex){x0,g_splash_h,0,u,.375f};
            gl->VertexPointer(3,0x1406,sizeof *edge,edge);
            gl->TexCoordPointer(2,0x1406,sizeof *edge,&edge[0].u);
            gl->DrawElements(5,4,0x1403,indices);
        }
        gl->VertexPointer(3,0x1406,sizeof *q,q);
        gl->TexCoordPointer(2,0x1406,sizeof *q,&q[0].u);
    }
}

static void menu_draw_pass(aot_cpu *c)
{
    aot_cpu entry = *c;
    uint32_t sp = c->r[13], args[8];
    unsigned i;
    for (i = 0; i < 8; ++i) args[i] = AOT_LD32(sp + 4u*i);
    invalidate_colour_cache();
    F_0007c8b8_orig(c);
    *c = entry;
    for (i = 0; i < 8; ++i) AOT_ST32(sp + 4u*i, args[i]);
}

static void menu_responsive(aot_cpu *c, float w, float h)
{
    menu_vertex *v = (menu_vertex *)aot_host(c->r[0]);
    menu_vertex base[MENU_VERTS];
    const float fixed = h / MENU_VISIBLE_H;
    const float added = w - MENU_VISIBLE_W * fixed;
    unsigned i, part;
    memcpy(base, v, sizeof base);
    const int nameentry = port_nameentry_active();
    const int layered = g_splash_art[ART_MENU_FRAME].pixels && g_splash_art[ART_BACKGROUND].pixels;
    g_menu_vertices=v; g_menu_w=w; g_menu_h=h; g_menu_index_buffer=c->r[1];

    /* The original artwork's natural visible area is 480x320. Split it on
     * the diagonal beside the left ornament (x=204 at the top, x=260 at the
     * bottom). The complete left and right compositions keep their source
     * scale; only the transparent interior and thin frame outline grow.
     * This also moves the baked logo right without rescaling it. */
    for (part = 0; part < 3; ++part) {
        for (i = 0; i < MENU_VERTS; ++i) {
            /* HighScore animates the original backdrop UVs inward by 0.09.
             * Those cropped UVs cannot define the boundaries of three pieces:
             * they leave diagonal holes and crop the frame. Its column-major
             * grid instead samples the same authored 480x320 canvas as menus. */
            float t = nameentry || layered ? (float)(i / MENU_GRID) / (MENU_GRID - 1)
                               : base[i].u * MENU_TEX_W / MENU_VISIBLE_W;
            float sy = nameentry || layered ? (float)(i % MENU_GRID) * MENU_VISIBLE_H / (MENU_GRID - 1)
                                : (1.0f - base[i].v) * MENU_TEX_W;
            float cut = 204.0f + 56.0f * sy / MENU_VISIBLE_H;
            float left_end = cut - 6.0f, right_start = cut + 6.0f;
            float sx, dx;
            if (part == 0) { /* transparent interior and thin frame outline */
                /* In the header, x>=215 belongs to the logo's green cap.
                 * Keep those pixels in the intact right piece; interpolating
                 * them across the widened strip creates a green smear. */
                float fill_end = sy <= 48.0f ? fminf(right_start, 214.0f)
                                                : right_start;
                sx = left_end + (fill_end-left_end)*t;
                dx = left_end * fixed + (added + 12.0f * fixed) * t;
            } else if (part == 1) { /* intact left structure */
                sx = left_end * t;
                dx = sx * fixed;
            } else { /* intact logo, right frame and rounded corner */
                sx = right_start + (MENU_VISIBLE_W - right_start) * t;
                dx = sx * fixed + added;
            }
            v[i].x = dx;
            v[i].y = nameentry || layered ? sy * fixed : base[i].y;
            v[i].z = base[i].z;
            v[i].u = sx / MENU_TEX_W;
            v[i].v = layered ? sy/MENU_TEX_W : nameentry ? 1.0f-sy/MENU_TEX_W : base[i].v;
        }
        g_menu_layer = layered ? (part == 0 ? 1 : 2) : 0;
        menu_draw_pass(c);
        g_menu_layer = 0;
        if (layered) aot_cfg->gl->Disable(0x0be2);
    }
    memcpy(v, base, sizeof base);
    if (layered) AOT_ST32(port_sym("gBindTextureRefLast"),0xffffffffu);
}

/* G0RenderBackdrop(cGLVertexUV*, vbo, count, cRTexture*), v7a:0x7c8b8:
 * the scrolling full-screen backdrop, drawn from a grid of screen-pixel
 * vertices built by cRBackdrop::Render. */
void F_0007c8b8(aot_cpu *c)
{
    canvas cv;
    /* The decorative menu frame fills the viewport in name entry too.
     * Its intact edge pieces keep their proportions; only the panel grows.
     * Interactive name-entry art and hitboxes still share the safe canvas. */
    if ((g_set.fit == SM_PORT_FIT_ADAPTIVE || port_nameentry_active()) && screen(&cv.w, &cv.h)
            && cv.w / cv.h >= 16.0f / 9.0f
            && strcmp((const char *)aot_host(c->r[3] + 12u),
                      "Backgrounds/MenuScreenHoriz.png") == 0) {
        g_menu_backdrop = 1;
        menu_responsive(c, cv.w, cv.h);
        return;
    }
    const char *backdrop_name = aot_host(c->r[3]+12u);
    int star_map=!strcmp(backdrop_name,"Backgrounds/Starmapbg.jpg") ||
        !strcmp(backdrop_name,"Backgrounds/Starmapprobg.jpg");
    if (canvas_get(&cv) && star_map && g_splash_art[ART_STAR_EXTENDED].pixels) {
        {
            menu_vertex *vertices=aot_host(c->r[0]), base[MENU_VERTS];
            memcpy(base,vertices,sizeof base);
            for(int i=0;i<MENU_VERTS;++i) {
                float x=(float)(i/MENU_GRID)/(MENU_GRID-1),y=(float)(i%MENU_GRID)/(MENU_GRID-1);
                float scale=fmaxf(cv.w/g_splash_art[ART_STAR_EXTENDED].w,cv.h/g_splash_art[ART_STAR_EXTENDED].h);
                float u=(1-cv.w/(scale*g_splash_art[ART_STAR_EXTENDED].w))*.5f;
                float v=(1-cv.h/(scale*g_splash_art[ART_STAR_EXTENDED].h))*.5f;
                vertices[i]=(menu_vertex){x*cv.w,y*cv.h,base[i].z,u+x*(1-2*u),v+y*(1-2*v)};
            }
            g_menu_vertices=vertices;g_space_layer=1;g_galaxy_backdrop=1;
            invalidate_colour_cache();F_0007c8b8_orig(c);
            g_space_layer=0;memcpy(vertices,base,sizeof base);
            AOT_ST32(port_sym("gBindTextureRefLast"),0xffffffffu);
            return;
        }
    }
    if (port_nameentry_active() && canvas_get(&cv)) {
        g_canvas_proj = 1;
        F_0007c8b8_orig(c);
        g_canvas_proj = 0;
        return;
    }
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
    const char *name = aot_host(c->r[0]+5u);
    g_splash_slot = strstr(name, "SANDLOTLOADING") || strstr(name, "SandlotLoading") ? 0
                  : strstr(name, "ALPHA72GAMESLOADING") || strstr(name, "Alpha72GamesLoading") ? 1
                  : strstr(name, "SPRITES/LOADING.PNG") || strstr(name, "Sprites/Loading.png") ? -1 : -2;
    g_splash_w = cv.w; g_splash_h = cv.h;
    g_splash_proj = g_splash_slot < 0 || !g_splash_art[g_splash_slot].pixels;
    g_splash_first_draw = 1;
    F_00079cf8_orig(c);
    g_splash_proj = 0; g_splash_first_draw = 0;
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
    float ox, oy;
    int i;
    if (!canvas_get(&cv)) {
        return;
    }
    ox = cv.ox;
    oy = cv.oy;
    aot_lock_acquire(&g_touch_lock);
    for (i = 0; i < g_touch_n; ++i) {
        const rect *r = &g_touch[i].r;
        if (!cv.nameentry && *x >= r->x0 && *x <= r->x1 && *y >= r->y0 && *y <= r->y1) {
            ox = g_touch[i].ox;
            oy = g_touch[i].oy;
            break;
        }
    }
    aot_lock_release(&g_touch_lock);
    /* screen pixel -> logical (inverse of the element's placement) -> the
     * pixel that the original's x * 640/W scaling turns into that logical x */
    *x = (*x - ox) / cv.tx * cv.sx;
    *y = (*y - oy) / cv.ty * cv.sy;
}

void sm_port_frame_begin(void)
{
    float w, h;
    if (screen(&w, &h)) port_nameentry_snapshot(w, h);
    install_filters();
    if (g_hud.anchored_gen != g_hud.gen) {
        /* last frame had no anchored elements: touches use the centred canvas */
        aot_lock_acquire(&g_touch_lock);
        g_touch_n = 0;
        aot_lock_release(&g_touch_lock);
    }
    ++g_hud.gen;
    g_ui_snapshot_count=0;
    g_menu_backdrop = 0;g_galaxy_backdrop=0;g_galaxy_detail_dx=0;g_galaxy_detail_dy=0;
    g_3d_draws_prev = g_3d_draws;
    g_3d_draws = 0;
}

/* Menus set up their 3D cameras but draw nothing with them; gameplay (and the
 * intro) draws its scene over the backdrop. Measured per frame with the GL
 * trace (docs/PORTING_STATUS.md, display adaptation). */
int port_backdrop_fills_screen(void) { return g_3d_draws_prev > 0; }
