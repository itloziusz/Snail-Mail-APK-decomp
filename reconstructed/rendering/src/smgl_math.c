/*
 * GLES 1.1 matrix math (see include/sm_rendering/smgl_math.h and
 * docs/RENDERING.md §3). Formulas are those of the OpenGL ES 1.1 spec
 * (§2.10.2, identical to OpenGL 1.5 §2.11.2).
 */
#include "sm_rendering/smgl_math.h"

#include <math.h>
#include <string.h>

#define SMGL_PI_F 3.14159265358979323846f

void smgl_mat4_identity(smgl_mat4 *out)
{
    memset(out->m, 0, sizeof out->m);
    out->m[0] = 1.0f;
    out->m[5] = 1.0f;
    out->m[10] = 1.0f;
    out->m[15] = 1.0f;
}

void smgl_mat4_mul(smgl_mat4 *out, const smgl_mat4 *a, const smgl_mat4 *b)
{
    smgl_mat4 r;
    for (int c = 0; c < 4; ++c) {
        for (int row = 0; row < 4; ++row) {
            float s = a->m[0 * 4 + row] * b->m[c * 4 + 0];
            s = s + a->m[1 * 4 + row] * b->m[c * 4 + 1];
            s = s + a->m[2 * 4 + row] * b->m[c * 4 + 2];
            s = s + a->m[3 * 4 + row] * b->m[c * 4 + 3];
            r.m[c * 4 + row] = s;
        }
    }
    *out = r;
}

void smgl_mat4_mul_array(smgl_mat4 *m, const float b[16])
{
    smgl_mat4 bm;
    memcpy(bm.m, b, sizeof bm.m);
    smgl_mat4_mul(m, m, &bm);
}

void smgl_mat4_rotation(smgl_mat4 *out, float angle_deg, float x, float y, float z)
{
    smgl_mat4_identity(out);
    const float rad = angle_deg * (SMGL_PI_F / 180.0f);
    const float s = sinf(rad);
    const float c = cosf(rad);
    float *m = out->m;

    /* Axis-aligned fast paths: the general formula gives the same entries
     * except that the diagonal element on the axis becomes (1-c)+c, which
     * can differ from 1 by an ulp. */
    if (x == 0.0f && y == 0.0f && z != 0.0f) {
        const float sz = (z > 0.0f) ? s : -s;
        m[0] = c;   m[4] = -sz;
        m[1] = sz;  m[5] = c;
        return;
    }
    if (y == 0.0f && z == 0.0f && x != 0.0f) {
        const float sx = (x > 0.0f) ? s : -s;
        m[5] = c;   m[9] = -sx;
        m[6] = sx;  m[10] = c;
        return;
    }
    if (x == 0.0f && z == 0.0f && y != 0.0f) {
        const float sy = (y > 0.0f) ? s : -s;
        m[0] = c;   m[8] = sy;
        m[2] = -sy; m[10] = c;
        return;
    }

    const float len = sqrtf(x * x + y * y + z * z);
    if (!(len > 0.0f) || !isfinite(len)) {
        return; /* degenerate axis: identity */
    }
    x = x / len;
    y = y / len;
    z = z / len;
    const float one_c = 1.0f - c;
    const float xx = x * x, yy = y * y, zz = z * z;
    const float xy = x * y, yz = y * z, zx = z * x;
    const float xs = x * s, ys = y * s, zs = z * s;

    /* Row-major reading of the spec matrix, stored column-major. */
    m[0] = xx * one_c + c;  m[4] = xy * one_c - zs; m[8] = zx * one_c + ys;
    m[1] = xy * one_c + zs; m[5] = yy * one_c + c;  m[9] = yz * one_c - xs;
    m[2] = zx * one_c - ys; m[6] = yz * one_c + xs; m[10] = zz * one_c + c;
}

void smgl_mat4_rotate(smgl_mat4 *m, float angle_deg, float x, float y, float z)
{
    smgl_mat4 r;
    smgl_mat4_rotation(&r, angle_deg, x, y, z);
    smgl_mat4_mul(m, m, &r);
}

/* Scale and translate multiply out the product with the zero entries of S/T
 * dropped (as Mesa does); for finite inputs this equals the full product
 * except possibly for the sign of a zero. */
void smgl_mat4_scale(smgl_mat4 *m, float x, float y, float z)
{
    for (int r = 0; r < 4; ++r) {
        m->m[0 * 4 + r] = m->m[0 * 4 + r] * x;
        m->m[1 * 4 + r] = m->m[1 * 4 + r] * y;
        m->m[2 * 4 + r] = m->m[2 * 4 + r] * z;
    }
}

void smgl_mat4_translate(smgl_mat4 *m, float x, float y, float z)
{
    for (int r = 0; r < 4; ++r) {
        float s = m->m[0 * 4 + r] * x;
        s = s + m->m[1 * 4 + r] * y;
        s = s + m->m[2 * 4 + r] * z;
        m->m[3 * 4 + r] = s + m->m[3 * 4 + r];
    }
}

int smgl_mat4_frustum(smgl_mat4 *m, float l, float r, float b, float t, float n, float f)
{
    if (n <= 0.0f || f <= 0.0f || l == r || b == t || n == f) {
        return -1;
    }
    smgl_mat4 fm;
    memset(fm.m, 0, sizeof fm.m);
    fm.m[0] = (2.0f * n) / (r - l);
    fm.m[5] = (2.0f * n) / (t - b);
    fm.m[8] = (r + l) / (r - l);
    fm.m[9] = (t + b) / (t - b);
    fm.m[10] = -(f + n) / (f - n);
    fm.m[11] = -1.0f;
    fm.m[14] = -(2.0f * f * n) / (f - n);
    smgl_mat4_mul(m, m, &fm);
    return 0;
}

int smgl_mat4_ortho(smgl_mat4 *m, float l, float r, float b, float t, float n, float f)
{
    if (l == r || b == t || n == f) {
        return -1;
    }
    smgl_mat4 om;
    smgl_mat4_identity(&om);
    om.m[0] = 2.0f / (r - l);
    om.m[5] = 2.0f / (t - b);
    om.m[10] = -2.0f / (f - n);
    om.m[12] = -(r + l) / (r - l);
    om.m[13] = -(t + b) / (t - b);
    om.m[14] = -(f + n) / (f - n);
    smgl_mat4_mul(m, m, &om);
    return 0;
}

void smgl_mat4_transform(float out[4], const smgl_mat4 *m, const float v[4])
{
    for (int r = 0; r < 4; ++r) {
        float s = m->m[0 * 4 + r] * v[0];
        s = s + m->m[1 * 4 + r] * v[1];
        s = s + m->m[2 * 4 + r] * v[2];
        s = s + m->m[3 * 4 + r] * v[3];
        out[r] = s;
    }
}

void smgl_matstack_init(smgl_matstack *st, int max_depth)
{
    if (max_depth < 1) {
        max_depth = 1;
    }
    if (max_depth > SMGL_MATSTACK_CAPACITY) {
        max_depth = SMGL_MATSTACK_CAPACITY;
    }
    st->max_depth = max_depth;
    st->depth = 1;
    smgl_mat4_identity(&st->s[0]);
}

smgl_mat4 *smgl_matstack_top(smgl_matstack *st)
{
    return &st->s[st->depth - 1];
}

const smgl_mat4 *smgl_matstack_top_const(const smgl_matstack *st)
{
    return &st->s[st->depth - 1];
}

int smgl_matstack_push(smgl_matstack *st)
{
    if (st->depth >= st->max_depth) {
        return -1;
    }
    st->s[st->depth] = st->s[st->depth - 1];
    st->depth += 1;
    return 0;
}

int smgl_matstack_pop(smgl_matstack *st)
{
    if (st->depth <= 1) {
        return -1;
    }
    st->depth -= 1;
    return 0;
}
