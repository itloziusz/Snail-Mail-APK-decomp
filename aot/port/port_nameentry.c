/* Own only layout and input routing. Original cRBorder edits the name and
 * cRHighScore retains confirmation, persistence and next-screen behaviour. */
#include <stdio.h>
#include <string.h>
#include "aot_decls.h"
#include "aot_host.h"
#include "aot_platform.h"
#include "port_internal.h"
#include "nameentry_layout.h"

static uint32_t g_pad, g_highscore;
static uint32_t g_borders[64];
static int g_border_count, g_collect_borders;
static int g_name_screen, g_selected = -1, g_key_count;
static sm_name_key g_keys[SM_NAME_KEYS];
static float g_safe[4];
static aot_lock g_snapshot_lock = AOT_LOCK_INIT;
static float g_snapshot[2 + SM_NAME_KEYS * 9];
static int g_snapshot_count;
static int g_input_active;
int sm_port_nameentry_input_active(void) { return __atomic_load_n(&g_input_active, __ATOMIC_ACQUIRE); }

int port_nameentry_active(void) { return g_name_screen; }
int port_nameentry_hide_border(uint32_t border)
{
    int i, owned = 0;
    if (!g_name_screen || g_collect_borders) return 0;
    for (i = 0; i < g_border_count; ++i) if (g_borders[i] == border) owned = 1;
    /* BorderManager also updates surviving gameplay borders. Visibility must
     * follow the current scene's ownership, including labels queued by AI. */
    if (!owned) return 1;
    /* HighScore disables these actions while the keyboard is open but still
     * draws them underneath the alpha-blended keyboard. Keep the closed-pad
     * Submit/Cancel phase, without overlapping the open-pad composition. */
    return g_name_screen && g_highscore && g_pad && AOT_LD32(g_pad)
        && (border == AOT_LD32(g_highscore + 0x24u)
            || border == AOT_LD32(g_highscore + 0x28u));
}
void F_0004d11c(aot_cpu *c) /* BorderManager::GetBorder */
{
    F_0004d11c_orig(c);
    if (g_collect_borders) {
        if (g_border_count == 64) aot_fatal("name entry: too many scene borders");
        g_borders[g_border_count++] = c->r[0];
    }
}
void sm_port_safe_area(float l, float t, float r, float b)
{
    g_safe[0] = l; g_safe[1] = t; g_safe[2] = r; g_safe[3] = b;
}
sm_name_canvas port_nameentry_canvas(float w, float h)
{
    return sm_name_scene_fit(w, h, g_safe[0], g_safe[1], g_safe[2], g_safe[3]);
}
void port_nameentry_snapshot(float w, float h)
{
    sm_name_canvas cv = port_nameentry_canvas(w, h);
    float data[2 + SM_NAME_KEYS * 9];
    int i, n = g_name_screen && g_pad && AOT_LD32(g_pad) ? g_key_count : 0;
    data[0] = (float)n; data[1] = (float)g_selected;
    for (i = 0; i < n; ++i) {
        const sm_name_key *k = g_keys + i;
        float *d = data + 2 + i * 9;
        d[0] = k->code; d[1] = k->x0; d[2] = k->y0; d[3] = k->x1; d[4] = k->y1;
        d[5] = cv.x + k->x0 * cv.scale; d[6] = cv.y + k->y0 * cv.scale;
        d[7] = cv.x + k->x1 * cv.scale; d[8] = cv.y + k->y1 * cv.scale;
    }
    aot_lock_acquire(&g_snapshot_lock);
    g_snapshot_count = 2 + n * 9;
    memcpy(g_snapshot, data, sizeof(float) * g_snapshot_count);
    aot_lock_release(&g_snapshot_lock);
}
int sm_port_nameentry_debug(float *dst, int capacity)
{
    int n;
    aot_lock_acquire(&g_snapshot_lock);
    n = g_snapshot_count < capacity ? g_snapshot_count : capacity;
    memcpy(dst, g_snapshot, sizeof(float) * n);
    aot_lock_release(&g_snapshot_lock);
    return n;
}

void F_00056010(aot_cpu *c)
{
    /* second Init parameter is -1 for viewing scores; otherwise a new score
     * row needs a name (0x56064..0x56098). */
    g_name_screen = c->r[2] != UINT32_MAX;
    g_highscore = c->r[0];
    __atomic_store_n(&g_input_active, g_name_screen, __ATOMIC_RELEASE);
    g_selected = -1;
    g_border_count = 0; g_collect_borders = g_name_screen;
    F_00056010_orig(c);
    g_collect_borders = 0;
}
void F_00055ed4(aot_cpu *c)
{
    F_00055ed4_orig(c);
    g_name_screen = 0; g_selected = -1;
    __atomic_store_n(&g_input_active, 0, __ATOMIC_RELEASE);
}
void F_0003be24(aot_cpu *c)
{
    static uint32_t filename;
    uint32_t args[2], len, data;
    F_0003be24_orig(c);
    if (!filename) {
        const char *s = "KEYPAD/KEYPADMASK.TXT";
        filename = aot_malloc((uint32_t)strlen(s) + 1);
        memcpy(aot_host(filename), s, strlen(s) + 1);
    }
    len = aot_malloc(4);
    args[0] = filename; args[1] = len;
    data = aot_invoke(c, port_sym("_Z14RShellLoadFilePcPi"), args, 2, NULL);
    if (!data) aot_fatal("name entry: missing key mask");
    {
        uint32_t size = AOT_LD32(len), copy;
        if (size > 16384) aot_fatal("name entry: invalid key mask size");
        copy = aot_malloc(size + 1);
        memcpy(aot_host(copy), aot_host(data), size);
        AOT_ST8(copy + size, 0);
        g_key_count = sm_name_parse((const char *)aot_host(copy), g_keys, SM_NAME_KEYS);
        aot_free(copy);
    }
    aot_free(data); aot_free(len);
    if (g_key_count <= 0) aot_fatal("name entry: invalid key mask");
}
void F_0003bb7c(aot_cpu *c)
{
    g_pad = c->r[0]; g_selected = -1;
    F_0003bb7c_orig(c);
}

static void highlight(aot_cpu *c, int i)
{
    const sm_name_key *k;
    uint32_t a[5];
    if (i < 0 || i >= g_key_count) return;
    k = g_keys + i;
    /* The original glow is also queued in legacy logical space; the renderer
     * performs the same source-to-safe-canvas transformation as the bitmap. */
    a[0] = g_pad + 0x20;
    a[1] = aot_f2u(k->x0 / 480.0f * 640.0f);
    a[2] = aot_f2u(k->y0 / 320.0f * 480.0f);
    a[3] = aot_f2u((k->x1 - k->x0) / 480.0f * 640.0f);
    a[4] = aot_f2u((k->y1 - k->y0) / 320.0f * 480.0f);
    aot_invoke(c, port_sym("_ZN12cGlowManager7GetGlowEffff"), a, 5, NULL);
}
void F_0003b4dc(aot_cpu *c)
{
    int i;
    uint32_t game;
    float x, y;
    if (!g_name_screen) { F_0003b4dc_orig(c); return; }
    /* JNI mouse coordinates have already been inverted into 640x480 logical
     * space. Convert once to the art reference; preserve subpixel precision. */
    game = AOT_LD32(port_sym("Game"));
    /* Original AI truncates the mouse floats to int before KeyTest. Recover
     * those same sampled coordinates before truncation so rendered and touch
     * edges coincide even when the uniform scale is fractional. */
    x = aot_u2f(AOT_LD32(game + 0x234u)) * (480.0f / 640.0f);
    y = aot_u2f(AOT_LD32(game + 0x238u)) * (320.0f / 480.0f);
    i = sm_name_hit(g_keys, g_key_count, x, y);
    AOT_ST32(c->r[3], (uint32_t)i);
    c->r[0] = i < 0 ? 0 : g_keys[i].code;
    if (i >= 0) g_selected = i;
}
void F_0003bbe8(aot_cpu *c)
{
    uint32_t pad = c->r[0];
    F_0003bbe8_orig(c);
    if (g_name_screen && g_selected >= 0 && AOT_LD32(pad) == 2) highlight(c, g_selected);
}

int sm_port_nameentry_key(aot_cpu *c, int android_key)
{
    int dx = 0, dy = 0, i;
    uint32_t a[2], code;
    if (!g_name_screen || !g_pad || AOT_LD32(g_pad) != 2) return 0;
    if (android_key == 19) dy = -1;
    else if (android_key == 20) dy = 1;
    else if (android_key == 21) dx = -1;
    else if (android_key == 22) dx = 1;
    if (dx || dy) {
        g_selected = sm_name_next(g_keys, g_key_count, g_selected, dx, dy);
        highlight(c, g_selected);
        return 1;
    }
    /* Enter finishes typing; DPAD centre / controller A activates selection. */
    if (android_key == 66) {
        AOT_ST32(g_pad, 3); return 1;
    }
    if (android_key != 23 && android_key != 96) return 0;
    i = g_selected;
    if (i < 0) i = g_selected = sm_name_next(g_keys, g_key_count, -1, 0, 1);
    if (i < 0) return 1;
    if (g_keys[i].code == '#') { AOT_ST32(g_pad, 3); return 1; }
    a[0] = g_pad; a[1] = g_keys[i].code;
    code = aot_invoke(c, port_sym("_ZN7cKeyPad11ConvertCodeEc"), a, 2, NULL);
    if (code) aot_invoke(c, port_sym("_Z6KeySeth"), &code, 1, NULL);
    return 1;
}

void F_00013d78(aot_cpu *c)
{
    int key = (int)c->r[2];
    static uint32_t game_symbol;
    if (sm_port_nameentry_key(c, key)) return;
    if (!((key >= 29 && key <= 54) || (key >= 7 && key <= 16)
            || key == 62 || key == 67 || key == 66)) return;
    if (!game_symbol) game_symbol = port_sym("Game");
    if (AOT_LD32(game_symbol)) F_00013d78_orig(c);
}
