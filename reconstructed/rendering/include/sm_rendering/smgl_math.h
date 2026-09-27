/*
 * GLES 1.1 fixed-function matrix math, GL-free (docs/RENDERING.md §3).
 *
 * Matrices are column-major exactly as GLES 1.1 stores them: element (row r,
 * column c) is m[c * 4 + r], so m[12..14] hold the translation. Every
 * operation that GLES 1.1 defines as "multiply the current matrix by X"
 * post-multiplies: M = M * X. All arithmetic is single-precision float, in
 * the summation order of a plain row-by-column product (k = 0..3), and the
 * project builds with -ffp-contract=off, so no multiply-add is fused.
 */
#ifndef SM_RENDERING_SMGL_MATH_H
#define SM_RENDERING_SMGL_MATH_H

#ifdef __cplusplus
extern "C" {
#endif

typedef struct smgl_mat4 {
    float m[16]; /* column-major, m[c * 4 + r] */
} smgl_mat4;

/* Deepest stack this implementation can hold (per stack). */
#define SMGL_MATSTACK_CAPACITY 32

typedef struct smgl_matstack {
    smgl_mat4 s[SMGL_MATSTACK_CAPACITY];
    int depth;     /* number of matrices on the stack, >= 1; top = s[depth-1] */
    int max_depth; /* configured limit (<= SMGL_MATSTACK_CAPACITY) */
} smgl_matstack;

void smgl_mat4_identity(smgl_mat4 *out);

/* out = a * b. out may alias a and/or b. */
void smgl_mat4_mul(smgl_mat4 *out, const smgl_mat4 *a, const smgl_mat4 *b);

/* glMultMatrixf: m = m * b, b column-major (16 floats). */
void smgl_mat4_mul_array(smgl_mat4 *m, const float b[16]);

/* The GLES 1.1 rotation matrix for `angle_deg` degrees about (x, y, z).
 * The axis is normalised first. A zero-length axis yields the identity
 * (the GL spec leaves this case undefined; Mesa also leaves the matrix
 * unchanged). Axis-aligned axes use the exact {c, s} entries so the
 * untouched diagonal element is exactly 1. */
void smgl_mat4_rotation(smgl_mat4 *out, float angle_deg, float x, float y, float z);

/* glRotatef / glScalef / glTranslatef: m = m * R|S|T. */
void smgl_mat4_rotate(smgl_mat4 *m, float angle_deg, float x, float y, float z);
void smgl_mat4_scale(smgl_mat4 *m, float x, float y, float z);
void smgl_mat4_translate(smgl_mat4 *m, float x, float y, float z);

/* glFrustumf / glOrthof: m = m * F|O. Return 0, or -1 when GLES 1.1 raises
 * GL_INVALID_VALUE (m is then unchanged): frustum n<=0, f<=0, l==r, b==t,
 * n==f; ortho l==r, b==t, n==f. */
int smgl_mat4_frustum(smgl_mat4 *m, float l, float r, float b, float t, float n, float f);
int smgl_mat4_ortho(smgl_mat4 *m, float l, float r, float b, float t, float n, float f);

/* out = m * v (v, out are 4-vectors; out must not alias v). */
void smgl_mat4_transform(float out[4], const smgl_mat4 *m, const float v[4]);

/* Matrix stack with GLES 1.1 overflow/underflow semantics: push on a full
 * stack and pop on a stack of depth 1 return -1 and leave the stack
 * unchanged (the caller records GL_STACK_OVERFLOW / GL_STACK_UNDERFLOW). */
void smgl_matstack_init(smgl_matstack *st, int max_depth);
smgl_mat4 *smgl_matstack_top(smgl_matstack *st);
const smgl_mat4 *smgl_matstack_top_const(const smgl_matstack *st);
int smgl_matstack_push(smgl_matstack *st);
int smgl_matstack_pop(smgl_matstack *st);

#ifdef __cplusplus
}
#endif
#endif /* SM_RENDERING_SMGL_MATH_H */
