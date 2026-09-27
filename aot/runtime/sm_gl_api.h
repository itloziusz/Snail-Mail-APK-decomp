/*
 * The exact GLES 1.x entry points imported by the original libsnailmail.so
 * (v7a sha256 e43bc913...a466, 45 functions; analysis/native/gl_usage.json).
 *
 * The AOT runtime converts guest arguments (32-bit guest addresses, softfp
 * floats in core registers) into ordinary host arguments and calls a backend
 * through `sm_gl_backend`. Pointer arguments that GLES 1.1 interprets as buffer
 * offsets when a buffer object is bound (glVertexPointer, glTexCoordPointer,
 * glDrawElements indices) are passed exactly as a native GLES 1.1 application
 * would pass them: an offset cast to a pointer when a buffer is bound, a host
 * pointer otherwise. Backends therefore implement plain GLES 1.1 semantics.
 *
 * Backends: real libGLESv1_CM (Android), GLES1-on-GLES2 emulation
 * (reconstructed/rendering, used where no GLES1 driver exists), trace recorder.
 */
#ifndef SM_GL_API_H
#define SM_GL_API_H
#include <stddef.h>
#include <stdint.h>

#ifdef __cplusplus
extern "C" {
#endif

typedef unsigned int sm_GLenum;
typedef unsigned int sm_GLuint;
typedef int sm_GLint;
typedef int sm_GLsizei;
typedef unsigned int sm_GLbitfield;
typedef unsigned char sm_GLboolean;
typedef float sm_GLfloat;
typedef ptrdiff_t sm_GLsizeiptr;

/* X(name, return, (params), (args)) */
#define SM_GL_FUNCS(X) \
  X(BindBuffer, void, (sm_GLenum target, sm_GLuint buffer), (target, buffer)) \
  X(BindTexture, void, (sm_GLenum target, sm_GLuint texture), (target, texture)) \
  X(BlendFunc, void, (sm_GLenum sfactor, sm_GLenum dfactor), (sfactor, dfactor)) \
  X(BufferData, void, (sm_GLenum target, sm_GLsizeiptr size, const void *data, sm_GLenum usage), (target, size, data, usage)) \
  X(Clear, void, (sm_GLbitfield mask), (mask)) \
  X(ClearColor, void, (sm_GLfloat r, sm_GLfloat g, sm_GLfloat b, sm_GLfloat a), (r, g, b, a)) \
  X(ClearDepthf, void, (sm_GLfloat depth), (depth)) \
  X(Color4f, void, (sm_GLfloat r, sm_GLfloat g, sm_GLfloat b, sm_GLfloat a), (r, g, b, a)) \
  X(CullFace, void, (sm_GLenum mode), (mode)) \
  X(DeleteTextures, void, (sm_GLsizei n, const sm_GLuint *textures), (n, textures)) \
  X(DepthFunc, void, (sm_GLenum func), (func)) \
  X(DepthMask, void, (sm_GLboolean flag), (flag)) \
  X(DepthRangef, void, (sm_GLfloat zNear, sm_GLfloat zFar), (zNear, zFar)) \
  X(Disable, void, (sm_GLenum cap), (cap)) \
  X(DisableClientState, void, (sm_GLenum array), (array)) \
  X(DrawElements, void, (sm_GLenum mode, sm_GLsizei count, sm_GLenum type, const void *indices), (mode, count, type, indices)) \
  X(Enable, void, (sm_GLenum cap), (cap)) \
  X(EnableClientState, void, (sm_GLenum array), (array)) \
  X(Finish, void, (void), ()) \
  X(Fogf, void, (sm_GLenum pname, sm_GLfloat param), (pname, param)) \
  X(Fogfv, void, (sm_GLenum pname, const sm_GLfloat *params), (pname, params)) \
  X(Frustumf, void, (sm_GLfloat l, sm_GLfloat r, sm_GLfloat b, sm_GLfloat t, sm_GLfloat n, sm_GLfloat f), (l, r, b, t, n, f)) \
  X(GenBuffers, void, (sm_GLsizei n, sm_GLuint *buffers), (n, buffers)) \
  X(GenTextures, void, (sm_GLsizei n, sm_GLuint *textures), (n, textures)) \
  X(Hint, void, (sm_GLenum target, sm_GLenum mode), (target, mode)) \
  X(LineWidth, void, (sm_GLfloat width), (width)) \
  X(LoadIdentity, void, (void), ()) \
  X(MatrixMode, void, (sm_GLenum mode), (mode)) \
  X(MultMatrixf, void, (const sm_GLfloat *m), (m)) \
  X(Orthof, void, (sm_GLfloat l, sm_GLfloat r, sm_GLfloat b, sm_GLfloat t, sm_GLfloat n, sm_GLfloat f), (l, r, b, t, n, f)) \
  X(PixelStorei, void, (sm_GLenum pname, sm_GLint param), (pname, param)) \
  X(PopMatrix, void, (void), ()) \
  X(PushMatrix, void, (void), ()) \
  X(ReadPixels, void, (sm_GLint x, sm_GLint y, sm_GLsizei w, sm_GLsizei h, sm_GLenum format, sm_GLenum type, void *pixels), (x, y, w, h, format, type, pixels)) \
  X(Rotatef, void, (sm_GLfloat angle, sm_GLfloat x, sm_GLfloat y, sm_GLfloat z), (angle, x, y, z)) \
  X(Scalef, void, (sm_GLfloat x, sm_GLfloat y, sm_GLfloat z), (x, y, z)) \
  X(Scissor, void, (sm_GLint x, sm_GLint y, sm_GLsizei w, sm_GLsizei h), (x, y, w, h)) \
  X(ShadeModel, void, (sm_GLenum mode), (mode)) \
  X(TexCoordPointer, void, (sm_GLint size, sm_GLenum type, sm_GLsizei stride, const void *ptr), (size, type, stride, ptr)) \
  X(TexEnvf, void, (sm_GLenum target, sm_GLenum pname, sm_GLfloat param), (target, pname, param)) \
  X(TexImage2D, void, (sm_GLenum target, sm_GLint level, sm_GLint internalformat, sm_GLsizei w, sm_GLsizei h, sm_GLint border, sm_GLenum format, sm_GLenum type, const void *pixels), (target, level, internalformat, w, h, border, format, type, pixels)) \
  X(TexParameteri, void, (sm_GLenum target, sm_GLenum pname, sm_GLint param), (target, pname, param)) \
  X(Translatef, void, (sm_GLfloat x, sm_GLfloat y, sm_GLfloat z), (x, y, z)) \
  X(VertexPointer, void, (sm_GLint size, sm_GLenum type, sm_GLsizei stride, const void *ptr), (size, type, stride, ptr)) \
  X(Viewport, void, (sm_GLint x, sm_GLint y, sm_GLsizei w, sm_GLsizei h), (x, y, w, h))

typedef struct sm_gl_backend {
#define SM_GL_MEMBER(name, ret, params, args) ret (*name) params;
  SM_GL_FUNCS(SM_GL_MEMBER)
#undef SM_GL_MEMBER
} sm_gl_backend;

#ifdef __cplusplus
}
#endif
#endif /* SM_GL_API_H */
