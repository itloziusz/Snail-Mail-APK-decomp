/*
 * Options menu: Display and Controls pages built from the game's
 * own widgets (cRBorder), so they use its font, frames, highlight and click
 * sound like every other menu.
 *
 * Original behaviour (analysis/native/generated/decomp/v7a):
 *  - cROptions::Init (v7a:0x59ee4) creates 5 borders with
 *    cRBorderManager::GetBorder(Game+0xd14) + cRBorder::Init(flags, text, 0x14,
 *    x, y, colour, 2, xoffset): the Tilt/Touch toggle (+0x14), two volume
 *    sliders (+0x18, +0x1c), Back (+0x10) and the version label (+0x20),
 *    stacked with cRBorder::SetBelow.
 *  - cROptions::AI (v7a:0x5a318) polls bit 0x20 of border+0x194 (clicked),
 *    rewrites toggle text with Rstrcpy(border+0x2c4, ...), and on Back calls
 *    UnInit and returns to the parent menu (Game+0x15c = this->parent).
 *  - cROptions::UnInit (v7a:0x59e3c) kills the 5 borders and saves asm.cfg.
 *  - cRIntro::Init (v7a:0x574f8) places its "Help" button beside a centred
 *    column with the last Init argument (x offset from the centre, -210) and
 *    copies +0x230/+0x6f0 from the button whose row it shares.
 *
 * The Display page replaces the options page while open; its Back button
 * rebuilds the options page with the original Init.
 */
#include <string.h>

#include "aot_decls.h"
#include "aot_host.h"
#include "port_internal.h"

#define B_FLAGS_BUTTON 0x14u     /* flags of the options page's buttons */
#define B_FLAGS_SLIDER 0x900004u /* flags of the original volume sliders */
#define B_TYPE 0x14u             /* third Init argument used by every options border */
#define B_CLICKED 0x20u          /* border+0x194: set when the button was clicked */
#define B_OFF_FLAGS 0x194u
#define B_OFF_TEXT 0x2c4u
#define B_OFF_ROW 0x230u
#define B_OFF_Y 0x6f0u
#define B_OFF_SLIDER 0x170u
#define B_OFF_SLIDER_DRAW 0x174u
#define OPT_OFF_BACK 0x10u       /* cROptions: Back border */
#define STR_BACK 0x82f10u        /* "Back" in .rodata (v7a) */

enum { PAGE_CLOSED, PAGE_OPTIONS, PAGE_DISPLAY, PAGE_CONTROLS };

static int g_page = PAGE_CLOSED;
static uint32_t g_btn_display;                   /* on the options page */
static uint32_t g_btn_controls;
static uint32_t g_btn_refresh, g_btn_fov, g_btn_fit, g_btn_back; /* Display page */
static uint32_t g_btn_mode, g_slider_smoothness, g_controls_back;

/* ---------------------------------------------------------------- guest calls */
static uint32_t game(void)
{
    static uint32_t a;
    if (!a) {
        a = port_sym("Game");
    }
    return AOT_LD32(a);
}

static uint32_t call(aot_cpu *c, const char *sym, const uint32_t *args, int n)
{
    return aot_invoke(c, port_sym(sym), args, n, NULL);
}

static uint32_t guest_str(const char *s)
{
    uint32_t n = (uint32_t)strlen(s) + 1u, g = aot_malloc(n);
    memcpy(aot_host(g), s, n);
    return g;
}

static uint32_t border_new_flags(aot_cpu *c, uint32_t text, float x, float y, float xoffset, uint32_t flags)
{
    uint32_t mgr = game() + 0xd14u;
    uint32_t b = call(c, "_ZN15cRBorderManager9GetBorderEv", &mgr, 1);
    const uint32_t one = aot_f2u(1.0f);
    uint32_t a[12] = {b, flags, text, B_TYPE, aot_f2u(x), aot_f2u(y), one, one, one, one, 2u,
                      aot_f2u(xoffset)};
    call(c, "_ZN8cRBorder4InitEiPciff7tColourif", a, 12);
    return b;
}

static uint32_t border_new(aot_cpu *c, uint32_t text, float x, float y, float xoffset)
{
    return border_new_flags(c, text, x, y, xoffset, B_FLAGS_BUTTON);
}

static void border_below(aot_cpu *c, uint32_t b, uint32_t above)
{
    uint32_t a[2] = {b, above};
    call(c, "_ZN8cRBorder8SetBelowEPS_", a, 2);
}

static void border_kill(aot_cpu *c, uint32_t *b)
{
    if (*b) {
        uint32_t a[2] = {game() + 0xd14u, *b};
        call(c, "_ZN15cRBorderManager4KillEP8cRBorder", a, 2);
        *b = 0;
    }
}

static int border_clicked(uint32_t b)
{
    uint32_t f;
    if (!b) {
        return 0;
    }
    f = AOT_LD32(b + B_OFF_FLAGS);
    if (!(f & B_CLICKED)) {
        return 0;
    }
    AOT_ST32(b + B_OFF_FLAGS, f & ~B_CLICKED);
    return 1;
}

static void border_text(uint32_t b, const char *s)
{
    size_t n = strlen(s) + 1;
    memcpy(aot_host(b + B_OFF_TEXT), s, n);
}

/* ---------------------------------------------------------------- labels */
static const char *refresh_label(int v)
{
    return v == SM_PORT_REFRESH_120 ? "Refresh 120 Hz" : v == SM_PORT_REFRESH_VRR ? "Refresh VRR" : "Refresh 60 Hz";
}
static const char *fov_label(int v) { return v == SM_PORT_FOV_ADAPTIVE ? "FOV Adaptive" : "FOV Original"; }
static const char *fit_label(int v) { return v == SM_PORT_FIT_ADAPTIVE ? "Screen Adaptive" : "Screen Stretched"; }
static const char *mode_label(int v) { return v == SM_PORT_CONTROLS_SMOOTH ? "Smooth Controls" : "Legacy Controls"; }

static uint32_t label_str(const char *s)
{
    /* one guest copy per distinct label, kept for the process lifetime */
    static struct { const char *s; uint32_t g; } cache[16];
    int i;
    for (i = 0; i < 16 && cache[i].s; ++i) {
        if (cache[i].s == s) {
            return cache[i].g;
        }
    }
    if (i == 16) {
        aot_fatal("port menu: label cache full");
    }
    cache[i].s = s;
    cache[i].g = guest_str(s);
    return cache[i].g;
}

/* ---------------------------------------------------------------- pages */
static void options_page_extras(aot_cpu *c, uint32_t opt)
{
    uint32_t back = AOT_LD32(opt + OPT_OFF_BACK);
    g_btn_display = border_new(c, label_str("Display"), 0.0f, 0.0f, 190.0f);
    g_btn_controls = border_new(c, label_str("Controls"), 0.0f, 0.0f, -190.0f);
    /* share the Back button's row, as cRIntro::Init does for "Help" */
    AOT_ST32(g_btn_display + B_OFF_ROW, AOT_LD32(back + B_OFF_ROW));
    AOT_ST32(g_btn_display + B_OFF_Y, AOT_LD32(back + B_OFF_Y));
    AOT_ST32(g_btn_controls + B_OFF_ROW, AOT_LD32(back + B_OFF_ROW));
    AOT_ST32(g_btn_controls + B_OFF_Y, AOT_LD32(back + B_OFF_Y));
    g_page = PAGE_OPTIONS;
}

static int refresh_next(int v)
{
    unsigned modes = sm_port_refresh_modes();
    int i;
    for (i = 1; i <= 3; ++i) {
        int m = (v + i) % 3;
        if (modes & (1u << m)) {
            return m;
        }
    }
    return SM_PORT_REFRESH_60;
}

static void display_page_open(aot_cpu *c)
{
    sm_port_settings s;
    uint32_t first;
    sm_port_get(&s);
    /* same geometry as the options page: first button at (90, 95) + 8; the
     * Refresh button only when the platform can present more than 60 Hz */
    if (sm_port_refresh_modes() != (1u << SM_PORT_REFRESH_60)) {
        g_btn_refresh = border_new(c, label_str(refresh_label(s.refresh)), 90.0f, 95.0f, 0.0f);
        g_btn_fov = border_new(c, label_str(fov_label(s.fov)), 90.0f, 400.0f, 0.0f);
        border_below(c, g_btn_fov, g_btn_refresh);
        first = g_btn_refresh;
    } else {
        g_btn_fov = border_new(c, label_str(fov_label(s.fov)), 90.0f, 95.0f, 0.0f);
        first = g_btn_fov;
    }
    AOT_ST32(first + B_OFF_Y, aot_f2u(aot_u2f(AOT_LD32(first + B_OFF_Y)) + 8.0f));
    g_btn_fit = border_new(c, label_str(fit_label(s.fit)), 90.0f, 400.0f, 0.0f);
    border_below(c, g_btn_fit, g_btn_fov);
    g_btn_back = border_new(c, AOT_B + STR_BACK, 90.0f, 400.0f, 0.0f);
    border_below(c, g_btn_back, g_btn_fit);
    g_page = PAGE_DISPLAY;
}

static void display_page_close(aot_cpu *c)
{
    border_kill(c, &g_btn_refresh);
    border_kill(c, &g_btn_fov);
    border_kill(c, &g_btn_fit);
    border_kill(c, &g_btn_back);
}

static void display_page_ai(aot_cpu *c, uint32_t opt)
{
    sm_port_settings s;
    int changed = 0;
    sm_port_get(&s);
    if (border_clicked(g_btn_refresh)) {
        s.refresh = refresh_next(s.refresh);
        border_text(g_btn_refresh, refresh_label(s.refresh));
        changed = 1;
    }
    if (border_clicked(g_btn_fov)) {
        s.fov = s.fov == SM_PORT_FOV_ADAPTIVE ? SM_PORT_FOV_ORIGINAL : SM_PORT_FOV_ADAPTIVE;
        border_text(g_btn_fov, fov_label(s.fov));
        changed = 1;
    }
    if (border_clicked(g_btn_fit)) {
        s.fit = s.fit == SM_PORT_FIT_ADAPTIVE ? SM_PORT_FIT_STRETCH : SM_PORT_FIT_ADAPTIVE;
        border_text(g_btn_fit, fit_label(s.fit));
        changed = 1;
    }
    if (changed) {
        port_settings_changed(&s);
    }
    if (border_clicked(g_btn_back)) {
        uint32_t a = opt;
        display_page_close(c);
        g_page = PAGE_CLOSED;
        aot_invoke(c, AOT_B + 0x59ee4u, &a, 1, NULL); /* cROptions::Init (through the hook) */
    }
}

static void controls_page_open(aot_cpu *c)
{
    sm_port_settings s;
    uint32_t first;
    sm_port_get(&s);
    g_btn_mode = border_new(c, label_str(mode_label(s.controls)), 90.0f, 95.0f, 0.0f);
    first = g_btn_mode;
    AOT_ST32(first + B_OFF_Y, aot_f2u(aot_u2f(AOT_LD32(first + B_OFF_Y)) + 8.0f));
    g_slider_smoothness = border_new_flags(c, label_str("Smoothness"), 90.0f, 400.0f, 0.0f,
                                             B_FLAGS_SLIDER);
    border_below(c, g_slider_smoothness, g_btn_mode);
    AOT_ST32(g_slider_smoothness + B_OFF_SLIDER, aot_f2u(s.smoothness / 100.0f));
    AOT_ST32(g_slider_smoothness + B_OFF_SLIDER_DRAW, aot_f2u(s.smoothness / 100.0f));
    g_controls_back = border_new(c, AOT_B + STR_BACK, 90.0f, 400.0f, 0.0f);
    border_below(c, g_controls_back, g_slider_smoothness);
    g_page = PAGE_CONTROLS;
}

static void controls_page_close(aot_cpu *c)
{
    border_kill(c, &g_btn_mode);
    border_kill(c, &g_slider_smoothness);
    border_kill(c, &g_controls_back);
}

static void controls_page_ai(aot_cpu *c, uint32_t opt)
{
    sm_port_settings s;
    int changed = 0;
    float slider;
    int value;
    sm_port_get(&s);
    if (border_clicked(g_btn_mode)) {
        s.controls = s.controls == SM_PORT_CONTROLS_LEGACY ? SM_PORT_CONTROLS_SMOOTH
                                                           : SM_PORT_CONTROLS_LEGACY;
        border_text(g_btn_mode, mode_label(s.controls));
        changed = 1;
    }
    slider = aot_u2f(AOT_LD32(g_slider_smoothness + B_OFF_SLIDER));
    if (slider >= 0.0f && slider <= 1.0f) {
        value = (int)(slider * 100.0f + 0.5f);
        if (value != s.smoothness) {
            s.smoothness = value;
            changed = 1;
        }
    }
    if (changed) port_settings_changed(&s);
    if (border_clicked(g_controls_back)) {
        uint32_t a = opt;
        controls_page_close(c);
        g_page = PAGE_CLOSED;
        aot_invoke(c, AOT_B + 0x59ee4u, &a, 1, NULL);
    }
}

/* ---------------------------------------------------------------- hooks */
void F_00059ee4(aot_cpu *c) /* cROptions::Init */
{
    uint32_t opt = c->r[0];
    F_00059ee4_orig(c);
    options_page_extras(c, opt);
}

void F_00059e3c(aot_cpu *c) /* cROptions::UnInit */
{
    if (g_page == PAGE_DISPLAY) {
        /* the options page's borders were already killed when the Display
         * page opened; only the Display page is showing */
        display_page_close(c);
    } else if (g_page == PAGE_CONTROLS) {
        controls_page_close(c);
    } else {
        border_kill(c, &g_btn_display);
        border_kill(c, &g_btn_controls);
        F_00059e3c_orig(c);
    }
    g_page = PAGE_CLOSED;
}

void F_0005a318(aot_cpu *c) /* cROptions::AI */
{
    uint32_t opt = c->r[0];
    if (g_page == PAGE_DISPLAY) {
        display_page_ai(c, opt);
        return;
    }
    if (g_page == PAGE_CONTROLS) {
        controls_page_ai(c, opt);
        return;
    }
    F_0005a318_orig(c);
    if (g_page == PAGE_OPTIONS && border_clicked(g_btn_display)) {
        uint32_t a = opt;
        aot_invoke(c, AOT_B + 0x59e3cu, &a, 1, NULL); /* cROptions::UnInit (through the hook) */
        display_page_open(c);
    } else if (g_page == PAGE_OPTIONS && border_clicked(g_btn_controls)) {
        uint32_t a = opt;
        aot_invoke(c, AOT_B + 0x59e3cu, &a, 1, NULL);
        controls_page_open(c);
    }
}
