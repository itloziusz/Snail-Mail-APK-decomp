#include "nameentry_layout.h"
#include <math.h>
#include <stdio.h>
#include <string.h>

sm_name_canvas sm_name_fit(float w, float h, float l, float t, float r, float b)
{
    sm_name_canvas c;
    float aw = fmaxf(1.0f, w - l - r), ah = fmaxf(1.0f, h - t - b);
    c.scale = fminf(aw / SM_NAME_REF_W, ah / SM_NAME_REF_H);
    c.x = l + (aw - SM_NAME_REF_W * c.scale) * 0.5f;
    c.y = t + (ah - SM_NAME_REF_H * c.scale) * 0.5f;
    return c;
}

sm_name_canvas sm_name_scene_fit(float w, float h, float l, float t, float r, float b)
{
    /* Interior of the authored menu frame, in its 480x320 art space:
     * left ornament ends at x=40, logo/header at y=64, right/bottom margins
     * are 20. Edge pieces scale with height when the purple panel widens.
     * Intersect this interior with Android's surface-local safe area. */
    if (w / fmaxf(h, 1.0f) >= 16.0f / 9.0f) {
        float art_scale = h / 320.0f;
        l = fmaxf(l, 40.0f * art_scale);
        t = fmaxf(t, 64.0f * art_scale);
        r = fmaxf(r, 20.0f * art_scale);
        b = fmaxf(b, 20.0f * art_scale);
    }
    return sm_name_fit(w, h, l, t, r, b);
}

/* Asset rows use a literal space as a key code. '$' is the name field,
 * '!' is a shift action supported by ConvertCode but absent from this asset,
 * '@' is delete and '#' is return. Never infer a character from a row index. */
int sm_name_parse(const char *text, sm_name_key *keys, int capacity)
{
    int n = 0;
    const char *p = text;
    while (*p) {
        const char *end = strchr(p, '\n');
        char line[128], code;
        int x0, y0, x1, y1;
        size_t len = end ? (size_t)(end - p) : strlen(p);
        if (len < sizeof line) {
            memcpy(line, p, len); line[len] = 0;
            if (sscanf(line, "%c - %d %d %d %d", &code, &x0, &y0, &x1, &y1) == 5) {
                if (n >= capacity || x0 < 0 || x1 > 480 || y0 < 0 || y1 > 256
                        || x1 <= x0 || y1 <= y0) return -1;
                keys[n++] = (sm_name_key){(float)x0, 64.0f + y0, (float)x1, 64.0f + y1,
                                         (unsigned char)code};
            }
        }
        if (!end) break;
        p = end + 1;
    }
    return n;
}

int sm_name_hit(const sm_name_key *keys, int n, float x, float y)
{
    int i;
    for (i = 0; i < n; ++i) {
        const sm_name_key *k = keys + i;
        if (k->code != '$' && x >= k->x0 && x < k->x1 && y >= k->y0 && y < k->y1) return i;
    }
    return -1;
}

/* Row navigation uses the visible centres, including the wide Space/Return
 * keys and staggered letter rows. At an edge stay put; no invisible wrap. */
int sm_name_next(const sm_name_key *keys, int n, int selected, int dx, int dy)
{
    int i, best = selected;
    float primary = INFINITY, secondary = INFINITY;
    float x, y;
    if (selected < 0 || selected >= n || keys[selected].code == '$') {
        for (i = 0; i < n; ++i) if (keys[i].code != '$') return i;
        return -1;
    }
    x = (keys[selected].x0 + keys[selected].x1) * 0.5f;
    y = (keys[selected].y0 + keys[selected].y1) * 0.5f;
    for (i = 0; i < n; ++i) {
        float cx, cy, p, s;
        if (keys[i].code == '$' || i == selected) continue;
        cx = (keys[i].x0 + keys[i].x1) * 0.5f;
        cy = (keys[i].y0 + keys[i].y1) * 0.5f;
        if (dx) { if (fabsf(cy - y) > 0.5f) continue; p = (cx - x) * dx; s = 0; }
        else { p = (cy - y) * dy; s = fabsf(cx - x); }
        if (p <= 0.5f) continue;
        if (p < primary - 0.5f || (fabsf(p - primary) <= 0.5f && s < secondary)) {
            primary = p; secondary = s; best = i;
        }
    }
    return best;
}
