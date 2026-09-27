/*
 * sm_rendering_math vs. hand-computed matrices (GLES 1.1 spec formulas).
 * No GL needed; runs in every build including the AArch64 cross build.
 *
 * Tolerances: products of exactly representable inputs are compared exactly
 * (every intermediate is exact in binary32). Rotations involve sinf/cosf of a
 * float angle; the expected entries are the exact trigonometric values and
 * the tolerance 1e-6 covers the float rounding of the angle conversion
 * (|cosf(float(pi/2))| ~ 4.4e-8) plus a few ulp of accumulation.
 */
#include "sm_rendering/smgl_math.h"

#include <string.h>

#include "smgl_test.h"

static void set_rows(smgl_mat4 *m, const float rows[16])
{
    for (int r = 0; r < 4; ++r) {
        for (int c = 0; c < 4; ++c) {
            m->m[c * 4 + r] = rows[r * 4 + c];
        }
    }
}

static void check_mat_exact(const char *what, const smgl_mat4 *got, const float rows[16])
{
    int bad = 0;
    for (int r = 0; r < 4; ++r) {
        for (int c = 0; c < 4; ++c) {
            if (got->m[c * 4 + r] != rows[r * 4 + c]) {
                if (!bad) {
                    fprintf(stderr, "%s: element (%d,%d) = %.9g, expected %.9g\n", what, r, c,
                            (double)got->m[c * 4 + r], (double)rows[r * 4 + c]);
                }
                bad = 1;
            }
        }
    }
    CHECK(!bad);
}

static void check_mat_near(const char *what, const smgl_mat4 *got, const float rows[16], float tol)
{
    int bad = 0;
    for (int r = 0; r < 4; ++r) {
        for (int c = 0; c < 4; ++c) {
            if (!(fabsf(got->m[c * 4 + r] - rows[r * 4 + c]) <= tol)) {
                if (!bad) {
                    fprintf(stderr, "%s: element (%d,%d) = %.9g, expected %.9g\n", what, r, c,
                            (double)got->m[c * 4 + r], (double)rows[r * 4 + c]);
                }
                bad = 1;
            }
        }
    }
    CHECK(!bad);
}

static void test_identity_and_layout(void)
{
    smgl_mat4 m;
    smgl_mat4_identity(&m);
    static const float id[16] = { 1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1 };
    check_mat_exact("identity", &m, id);

    /* Column-major: glTranslatef's offsets land in m[12..14]. */
    smgl_mat4_translate(&m, 1.0f, 2.0f, 3.0f);
    CHECK(m.m[12] == 1.0f && m.m[13] == 2.0f && m.m[14] == 3.0f && m.m[15] == 1.0f);
}

static void test_mul(void)
{
    /* A and B given row-major; A*B and B*A worked out by hand. */
    static const float a_rows[16] = { 1, 2, 0, 0, 0, 1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1 };
    static const float b_rows[16] = { 1, 0, 0, 0, 3, 1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1 };
    static const float ab[16] = { 7, 2, 0, 0, 3, 1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1 };
    static const float ba[16] = { 1, 2, 0, 0, 3, 7, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1 };
    smgl_mat4 a, b, r;
    set_rows(&a, a_rows);
    set_rows(&b, b_rows);
    smgl_mat4_mul(&r, &a, &b);
    check_mat_exact("A*B", &r, ab);
    smgl_mat4_mul(&r, &b, &a);
    check_mat_exact("B*A", &r, ba);
    /* Aliasing out == a. */
    smgl_mat4_mul(&a, &a, &b);
    check_mat_exact("A*=B (aliased)", &a, ab);

    /* A general 4x4 product: rows (1..16) times (16..1), computed by hand. */
    static const float p_rows[16] = { 1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14, 15, 16 };
    static const float q_rows[16] = { 16, 15, 14, 13, 12, 11, 10, 9, 8, 7, 6, 5, 4, 3, 2, 1 };
    static const float pq[16] = { 80, 70, 60, 50, 240, 214, 188, 162,
                                  400, 358, 316, 274, 560, 502, 444, 386 };
    smgl_mat4 p, q;
    set_rows(&p, p_rows);
    set_rows(&q, q_rows);
    smgl_mat4_mul(&r, &p, &q);
    check_mat_exact("P*Q", &r, pq);

    /* glMultMatrixf post-multiplies: T(1,0,0) * S(2) maps (1,1,1) to (3,2,2). */
    smgl_mat4 m;
    smgl_mat4_identity(&m);
    smgl_mat4_translate(&m, 1.0f, 0.0f, 0.0f);
    const float s2[16] = { 2, 0, 0, 0, 0, 2, 0, 0, 0, 0, 2, 0, 0, 0, 0, 1 }; /* column-major */
    smgl_mat4_mul_array(&m, s2);
    const float v[4] = { 1, 1, 1, 1 };
    float o[4];
    smgl_mat4_transform(o, &m, v);
    CHECK(o[0] == 3.0f && o[1] == 2.0f && o[2] == 2.0f && o[3] == 1.0f);
}

static void test_scale_translate(void)
{
    smgl_mat4 m;
    smgl_mat4_identity(&m);
    smgl_mat4_translate(&m, 1.0f, 2.0f, 3.0f);
    smgl_mat4_scale(&m, 2.0f, 3.0f, 4.0f);
    static const float ts[16] = { 2, 0, 0, 1, 0, 3, 0, 2, 0, 0, 4, 3, 0, 0, 0, 1 };
    check_mat_exact("T*S", &m, ts);

    smgl_mat4_identity(&m);
    smgl_mat4_scale(&m, 2.0f, 3.0f, 4.0f);
    smgl_mat4_translate(&m, 1.0f, 2.0f, 3.0f);
    static const float st[16] = { 2, 0, 0, 2, 0, 3, 0, 6, 0, 0, 4, 12, 0, 0, 0, 1 };
    check_mat_exact("S*T", &m, st);

    /* The game's short-vertex format: glScalef(1/128) is exact for int16. */
    smgl_mat4_identity(&m);
    smgl_mat4_scale(&m, 0.0078125f, 0.0078125f, 0.0078125f);
    const float v[4] = { -32768.0f, 32767.0f, 100.0f, 1.0f };
    float o[4];
    smgl_mat4_transform(o, &m, v);
    CHECK(o[0] == -256.0f && o[1] == 255.9921875f && o[2] == 0.78125f && o[3] == 1.0f);
}

static void test_rotate(void)
{
    smgl_mat4 m;
    /* 90 degrees about +z: [[c,-s],[s,c]] with c = 0, s = 1. */
    smgl_mat4_identity(&m);
    smgl_mat4_rotate(&m, 90.0f, 0.0f, 0.0f, 1.0f);
    static const float rz90[16] = { 0, -1, 0, 0, 1, 0, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1 };
    check_mat_near("Rz(90)", &m, rz90, 1e-6f);
    CHECK(m.m[10] == 1.0f && m.m[15] == 1.0f); /* axis entry exact */

    /* About -z equals the negative angle about +z. */
    smgl_mat4_identity(&m);
    smgl_mat4_rotate(&m, 90.0f, 0.0f, 0.0f, -1.0f);
    static const float rzm90[16] = { 0, 1, 0, 0, -1, 0, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1 };
    check_mat_near("R-z(90)", &m, rzm90, 1e-6f);

    /* 90 about x: y -> z; 90 about y: z -> x. */
    smgl_mat4_identity(&m);
    smgl_mat4_rotate(&m, 90.0f, 1.0f, 0.0f, 0.0f);
    static const float rx90[16] = { 1, 0, 0, 0, 0, 0, -1, 0, 0, 1, 0, 0, 0, 0, 0, 1 };
    check_mat_near("Rx(90)", &m, rx90, 1e-6f);
    smgl_mat4_identity(&m);
    smgl_mat4_rotate(&m, 90.0f, 0.0f, 1.0f, 0.0f);
    static const float ry90[16] = { 0, 0, 1, 0, 0, 1, 0, 0, -1, 0, 0, 0, 0, 0, 0, 1 };
    check_mat_near("Ry(90)", &m, ry90, 1e-6f);

    /* 120 degrees about (1,1,1) permutes the axes x->y->z->x; the axis is
     * normalised, so (2,2,2) gives the same matrix. General formula path. */
    static const float r120[16] = { 0, 0, 1, 0, 1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 0, 1 };
    smgl_mat4_identity(&m);
    smgl_mat4_rotate(&m, 120.0f, 1.0f, 1.0f, 1.0f);
    check_mat_near("R(120,1,1,1)", &m, r120, 1e-6f);
    smgl_mat4_identity(&m);
    smgl_mat4_rotate(&m, 120.0f, 2.0f, 2.0f, 2.0f);
    check_mat_near("R(120,2,2,2)", &m, r120, 1e-6f);

    /* 60 degrees about +z: c = 1/2, s = sqrt(3)/2. */
    smgl_mat4_identity(&m);
    smgl_mat4_rotate(&m, 60.0f, 0.0f, 0.0f, 1.0f);
    const float h = 0.86602540378f;
    const float rz60[16] = { 0.5f, -h, 0, 0, h, 0.5f, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1 };
    check_mat_near("Rz(60)", &m, rz60, 1e-6f);

    /* Zero axis: matrix unchanged. */
    smgl_mat4_identity(&m);
    smgl_mat4_translate(&m, 5.0f, 6.0f, 7.0f);
    smgl_mat4 before = m;
    smgl_mat4_rotate(&m, 45.0f, 0.0f, 0.0f, 0.0f);
    CHECK(memcmp(&before, &m, sizeof m) == 0);

    /* T(10,0,0) * Rz(90) maps (1,0,0) to (10,1,0). */
    smgl_mat4_identity(&m);
    smgl_mat4_translate(&m, 10.0f, 0.0f, 0.0f);
    smgl_mat4_rotate(&m, 90.0f, 0.0f, 0.0f, 1.0f);
    const float v[4] = { 1, 0, 0, 1 };
    float o[4];
    smgl_mat4_transform(o, &m, v);
    CHECK_NEAR(o[0], 10.0, 1e-6);
    CHECK_NEAR(o[1], 1.0, 1e-6);
    CHECK_NEAR(o[2], 0.0, 1e-6);
    CHECK_NEAR(o[3], 1.0, 0.0);
}

static void test_frustum_ortho(void)
{
    smgl_mat4 m;
    /* frustum(l=-1, r=3, b=-2, t=2, n=2, f=6): every entry exact:
     * 2n/(r-l)=1, 2n/(t-b)=1, (r+l)/(r-l)=0.5, (t+b)/(t-b)=0,
     * -(f+n)/(f-n)=-2, -2fn/(f-n)=-6. */
    smgl_mat4_identity(&m);
    CHECK_EQ_INT(smgl_mat4_frustum(&m, -1.0f, 3.0f, -2.0f, 2.0f, 2.0f, 6.0f), 0);
    static const float fr[16] = { 1, 0, 0.5f, 0, 0, 1, 0, 0, 0, 0, -2, -6, 0, 0, -1, 0 };
    check_mat_exact("frustum", &m, fr);

    /* The game's 2D projection glOrthof(0, W, H, 0, -1, 1), W = H = 64. */
    smgl_mat4_identity(&m);
    CHECK_EQ_INT(smgl_mat4_ortho(&m, 0.0f, 64.0f, 64.0f, 0.0f, -1.0f, 1.0f), 0);
    static const float orr[16] = { 0.03125f, 0, 0, -1, 0, -0.03125f, 0, 1, 0, 0, -1, 0, 0, 0, 0, 1 };
    check_mat_exact("ortho", &m, orr);
    const float v[4] = { 32.0f, 16.0f, 0.0f, 1.0f };
    float o[4];
    smgl_mat4_transform(o, &m, v);
    CHECK(o[0] == 0.0f && o[1] == 0.5f && o[2] == 0.0f && o[3] == 1.0f);

    /* ortho(-2, 2, -1, 3, 1, 5): 2/(r-l)=0.5, 2/(t-b)=0.5, -2/(f-n)=-0.5,
     * tx=0, ty=-(t+b)/(t-b)=-0.5, tz=-(f+n)/(f-n)=-1.5. */
    smgl_mat4_identity(&m);
    CHECK_EQ_INT(smgl_mat4_ortho(&m, -2.0f, 2.0f, -1.0f, 3.0f, 1.0f, 5.0f), 0);
    static const float or2[16] = { 0.5f, 0, 0, 0, 0, 0.5f, 0, -0.5f, 0, 0, -0.5f, -1.5f, 0, 0, 0, 1 };
    check_mat_exact("ortho2", &m, or2);

    /* GL_INVALID_VALUE cases leave the matrix unchanged. */
    smgl_mat4_identity(&m);
    smgl_mat4_translate(&m, 1.0f, 2.0f, 3.0f);
    smgl_mat4 before = m;
    CHECK_EQ_INT(smgl_mat4_frustum(&m, -1, 1, -1, 1, 0.0f, 10), -1);
    CHECK_EQ_INT(smgl_mat4_frustum(&m, -1, 1, -1, 1, 1, -10), -1);
    CHECK_EQ_INT(smgl_mat4_frustum(&m, 1, 1, -1, 1, 1, 10), -1);
    CHECK_EQ_INT(smgl_mat4_frustum(&m, -1, 1, 2, 2, 1, 10), -1);
    CHECK_EQ_INT(smgl_mat4_frustum(&m, -1, 1, -1, 1, 3, 3), -1);
    CHECK_EQ_INT(smgl_mat4_ortho(&m, 1, 1, -1, 1, -1, 1), -1);
    CHECK_EQ_INT(smgl_mat4_ortho(&m, -1, 1, 0, 0, -1, 1), -1);
    CHECK_EQ_INT(smgl_mat4_ortho(&m, -1, 1, -1, 1, 2, 2), -1);
    CHECK(memcmp(&before, &m, sizeof m) == 0);

    /* Frustum post-multiplies too: T * F. */
    smgl_mat4_identity(&m);
    smgl_mat4_translate(&m, 0.0f, 0.0f, 1.0f);
    CHECK_EQ_INT(smgl_mat4_frustum(&m, -1.0f, 3.0f, -2.0f, 2.0f, 2.0f, 6.0f), 0);
    /* Row 2 of T*F = row2(F) + 1*row3(F) = (0,0,-3,-6). */
    CHECK(m.m[2] == 0.0f && m.m[6] == 0.0f && m.m[10] == -3.0f && m.m[14] == -6.0f);
}

static void test_stack(void)
{
    smgl_matstack s;
    smgl_matstack_init(&s, 4);
    CHECK_EQ_INT(s.depth, 1);
    smgl_mat4_translate(smgl_matstack_top(&s), 1.0f, 2.0f, 3.0f);
    const smgl_mat4 t = *smgl_matstack_top(&s);

    CHECK_EQ_INT(smgl_matstack_push(&s), 0); /* push copies the top */
    CHECK(memcmp(smgl_matstack_top(&s), &t, sizeof t) == 0);
    smgl_mat4_scale(smgl_matstack_top(&s), 2.0f, 2.0f, 2.0f);
    CHECK_EQ_INT(smgl_matstack_pop(&s), 0); /* pop restores */
    CHECK(memcmp(smgl_matstack_top(&s), &t, sizeof t) == 0);

    CHECK_EQ_INT(smgl_matstack_push(&s), 0);
    CHECK_EQ_INT(smgl_matstack_push(&s), 0);
    CHECK_EQ_INT(smgl_matstack_push(&s), 0);
    CHECK_EQ_INT(s.depth, 4);
    smgl_mat4_scale(smgl_matstack_top(&s), 3.0f, 3.0f, 3.0f);
    const smgl_mat4 top4 = *smgl_matstack_top(&s);
    CHECK_EQ_INT(smgl_matstack_push(&s), -1); /* overflow: unchanged */
    CHECK_EQ_INT(s.depth, 4);
    CHECK(memcmp(smgl_matstack_top(&s), &top4, sizeof top4) == 0);
    CHECK_EQ_INT(smgl_matstack_pop(&s), 0);
    CHECK_EQ_INT(smgl_matstack_pop(&s), 0);
    CHECK_EQ_INT(smgl_matstack_pop(&s), 0);
    CHECK_EQ_INT(smgl_matstack_pop(&s), -1); /* underflow: unchanged */
    CHECK_EQ_INT(s.depth, 1);
    CHECK(memcmp(smgl_matstack_top(&s), &t, sizeof t) == 0);

    smgl_matstack_init(&s, 1000); /* clamped to the capacity */
    CHECK_EQ_INT(s.max_depth, SMGL_MATSTACK_CAPACITY);
}

int main(void)
{
    test_identity_and_layout();
    test_mul();
    test_scale_translate();
    test_rotate();
    test_frustum_ortho();
    test_stack();
    return smt_finish("test_smgl_math");
}
