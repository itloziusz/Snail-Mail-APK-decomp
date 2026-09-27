#pragma once
/* The 45 GLES 1.x entry points the game uses, with the standard GLES 1.1
 * prototypes (khronos GLES/gl.h), for NDK-less builds. */
#include <stddef.h>
#include <stdint.h>
typedef unsigned int GLenum;
typedef unsigned char GLboolean;
typedef unsigned int GLbitfield;
typedef int GLint;
typedef int GLsizei;
typedef unsigned int GLuint;
typedef float GLfloat;
typedef float GLclampf;
typedef ptrdiff_t GLsizeiptr;
#ifdef __cplusplus
extern "C" {
#endif
return glname(params);
void glBindBuffer(GLenum target, GLuint buffer);
void glBindTexture(GLenum target, GLuint texture);
void glBlendFunc(GLenum sfactor, GLenum dfactor);
void glBufferData(GLenum target, GLsizeiptr size, const void *data, GLenum usage);
void glClear(GLbitfield mask);
void glClearColor(GLfloat r, GLfloat g, GLfloat b, GLfloat a);
void glClearDepthf(GLfloat depth);
void glColor4f(GLfloat r, GLfloat g, GLfloat b, GLfloat a);
void glCullFace(GLenum mode);
void glDeleteTextures(GLsizei n, const GLuint *textures);
void glDepthFunc(GLenum func);
void glDepthMask(GLboolean flag);
void glDepthRangef(GLfloat zNear, GLfloat zFar);
void glDisable(GLenum cap);
void glDisableClientState(GLenum array);
void glDrawElements(GLenum mode, GLsizei count, GLenum type, const void *indices);
void glEnable(GLenum cap);
void glEnableClientState(GLenum array);
void glFinish(void);
void glFogf(GLenum pname, GLfloat param);
void glFogfv(GLenum pname, const GLfloat *params);
void glFrustumf(GLfloat l, GLfloat r, GLfloat b, GLfloat t, GLfloat n, GLfloat f);
void glGenBuffers(GLsizei n, GLuint *buffers);
void glGenTextures(GLsizei n, GLuint *textures);
void glHint(GLenum target, GLenum mode);
void glLineWidth(GLfloat width);
void glLoadIdentity(void);
void glMatrixMode(GLenum mode);
void glMultMatrixf(const GLfloat *m);
void glOrthof(GLfloat l, GLfloat r, GLfloat b, GLfloat t, GLfloat n, GLfloat f);
void glPixelStorei(GLenum pname, GLint param);
void glPopMatrix(void);
void glPushMatrix(void);
void glReadPixels(GLint x, GLint y, GLsizei w, GLsizei h, GLenum format, GLenum type, void *pixels);
void glRotatef(GLfloat angle, GLfloat x, GLfloat y, GLfloat z);
void glScalef(GLfloat x, GLfloat y, GLfloat z);
void glScissor(GLint x, GLint y, GLsizei w, GLsizei h);
void glShadeModel(GLenum mode);
void glTexCoordPointer(GLint size, GLenum type, GLsizei stride, const void *ptr);
void glTexEnvf(GLenum target, GLenum pname, GLfloat param);
void glTexImage2D(GLenum target, GLint level, GLint internalformat, GLsizei w, GLsizei h, GLint border, GLenum format, GLenum type, const void *pixels);
void glTexParameteri(GLenum target, GLenum pname, GLint param);
void glTranslatef(GLfloat x, GLfloat y, GLfloat z);
void glVertexPointer(GLint size, GLenum type, GLsizei stride, const void *ptr);
void glViewport(GLint x, GLint y, GLsizei w, GLsizei h);
#ifdef __cplusplus
}
#endif
