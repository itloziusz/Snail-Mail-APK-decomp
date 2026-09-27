#pragma once
/* The GLES 2.0 subset used by reconstructed/rendering/src/smgl.c (the
 * GLES1-on-GLES2 emulation linked into the aot-gles2 APK variant), for
 * NDK-less builds. Every constant value and prototype below is copied
 * verbatim from the Khronos-generated <GLES2/gl2.h> (registry 20220530, as
 * shipped by the NDK and by Mesa); only the needed entries are kept, so a
 * new use in smgl.c is a compile error until it is added here AND to
 * symbols/libGLESv2.txt. The types are those of <KHR/khrplatform.h> for LP64
 * Android (khronos_ssize_t = signed long, khronos_float_t = float), so no
 * khrplatform.h/gl2platform.h is needed. */
#ifdef __cplusplus
extern "C" {
#endif

#define GL_ES_VERSION_2_0 1
/* KHRONOS_APICALL / KHRONOS_APIENTRY on __ANDROID__ */
#define GL_APICALL __attribute__((visibility("default")))
#define GL_APIENTRY

typedef unsigned int GLenum;
typedef unsigned int GLuint;
typedef char GLchar;
typedef float GLfloat;
typedef signed long int GLsizeiptr;
typedef unsigned int GLbitfield;
typedef int GLint;
typedef unsigned char GLboolean;
typedef int GLsizei;

#define GL_FALSE 0
#define GL_TRIANGLE_FAN 0x0006
#define GL_ZERO 0
#define GL_ONE 1
#define GL_SRC_COLOR 0x0300
#define GL_ONE_MINUS_SRC_COLOR 0x0301
#define GL_SRC_ALPHA 0x0302
#define GL_ONE_MINUS_SRC_ALPHA 0x0303
#define GL_DST_ALPHA 0x0304
#define GL_ONE_MINUS_DST_ALPHA 0x0305
#define GL_DST_COLOR 0x0306
#define GL_ONE_MINUS_DST_COLOR 0x0307
#define GL_SRC_ALPHA_SATURATE 0x0308
#define GL_ARRAY_BUFFER 0x8892
#define GL_ELEMENT_ARRAY_BUFFER 0x8893
#define GL_STATIC_DRAW 0x88E4
#define GL_DYNAMIC_DRAW 0x88E8
#define GL_TEXTURE_2D 0x0DE1
#define GL_CULL_FACE 0x0B44
#define GL_BLEND 0x0BE2
#define GL_DITHER 0x0BD0
#define GL_STENCIL_TEST 0x0B90
#define GL_DEPTH_TEST 0x0B71
#define GL_SCISSOR_TEST 0x0C11
#define GL_POLYGON_OFFSET_FILL 0x8037
#define GL_SAMPLE_ALPHA_TO_COVERAGE 0x809E
#define GL_SAMPLE_COVERAGE 0x80A0
#define GL_NO_ERROR 0
#define GL_INVALID_ENUM 0x0500
#define GL_INVALID_VALUE 0x0501
#define GL_INVALID_OPERATION 0x0502
#define GL_OUT_OF_MEMORY 0x0505
#define GL_UNPACK_ALIGNMENT 0x0CF5
#define GL_PACK_ALIGNMENT 0x0D05
#define GL_MAX_TEXTURE_SIZE 0x0D33
#define GL_DONT_CARE 0x1100
#define GL_FASTEST 0x1101
#define GL_NICEST 0x1102
#define GL_GENERATE_MIPMAP_HINT 0x8192
#define GL_BYTE 0x1400
#define GL_UNSIGNED_BYTE 0x1401
#define GL_SHORT 0x1402
#define GL_UNSIGNED_SHORT 0x1403
#define GL_FLOAT 0x1406
#define GL_FIXED 0x140C
#define GL_ALPHA 0x1906
#define GL_RGB 0x1907
#define GL_RGBA 0x1908
#define GL_LUMINANCE 0x1909
#define GL_LUMINANCE_ALPHA 0x190A
#define GL_UNSIGNED_SHORT_4_4_4_4 0x8033
#define GL_UNSIGNED_SHORT_5_5_5_1 0x8034
#define GL_UNSIGNED_SHORT_5_6_5 0x8363
#define GL_FRAGMENT_SHADER 0x8B30
#define GL_VERTEX_SHADER 0x8B31
#define GL_LINK_STATUS 0x8B82
#define GL_REPLACE 0x1E01
#define GL_NEAREST 0x2600
#define GL_LINEAR 0x2601
#define GL_NEAREST_MIPMAP_NEAREST 0x2700
#define GL_LINEAR_MIPMAP_NEAREST 0x2701
#define GL_NEAREST_MIPMAP_LINEAR 0x2702
#define GL_LINEAR_MIPMAP_LINEAR 0x2703
#define GL_TEXTURE_MAG_FILTER 0x2800
#define GL_TEXTURE_MIN_FILTER 0x2801
#define GL_TEXTURE_WRAP_S 0x2802
#define GL_TEXTURE_WRAP_T 0x2803
#define GL_TEXTURE0 0x84C0
#define GL_REPEAT 0x2901
#define GL_CLAMP_TO_EDGE 0x812F
#define GL_COMPILE_STATUS 0x8B81

GL_APICALL void GL_APIENTRY glActiveTexture (GLenum texture);
GL_APICALL void GL_APIENTRY glAttachShader (GLuint program, GLuint shader);
GL_APICALL void GL_APIENTRY glBindAttribLocation (GLuint program, GLuint index, const GLchar *name);
GL_APICALL void GL_APIENTRY glBindBuffer (GLenum target, GLuint buffer);
GL_APICALL void GL_APIENTRY glBindTexture (GLenum target, GLuint texture);
GL_APICALL void GL_APIENTRY glBlendFunc (GLenum sfactor, GLenum dfactor);
GL_APICALL void GL_APIENTRY glBufferData (GLenum target, GLsizeiptr size, const void *data, GLenum usage);
GL_APICALL void GL_APIENTRY glClear (GLbitfield mask);
GL_APICALL void GL_APIENTRY glClearColor (GLfloat red, GLfloat green, GLfloat blue, GLfloat alpha);
GL_APICALL void GL_APIENTRY glClearDepthf (GLfloat d);
GL_APICALL void GL_APIENTRY glCompileShader (GLuint shader);
GL_APICALL GLuint GL_APIENTRY glCreateProgram (void);
GL_APICALL GLuint GL_APIENTRY glCreateShader (GLenum type);
GL_APICALL void GL_APIENTRY glCullFace (GLenum mode);
GL_APICALL void GL_APIENTRY glDeleteProgram (GLuint program);
GL_APICALL void GL_APIENTRY glDeleteShader (GLuint shader);
GL_APICALL void GL_APIENTRY glDeleteTextures (GLsizei n, const GLuint *textures);
GL_APICALL void GL_APIENTRY glDepthFunc (GLenum func);
GL_APICALL void GL_APIENTRY glDepthMask (GLboolean flag);
GL_APICALL void GL_APIENTRY glDepthRangef (GLfloat n, GLfloat f);
GL_APICALL void GL_APIENTRY glDisable (GLenum cap);
GL_APICALL void GL_APIENTRY glDisableVertexAttribArray (GLuint index);
GL_APICALL void GL_APIENTRY glDrawElements (GLenum mode, GLsizei count, GLenum type, const void *indices);
GL_APICALL void GL_APIENTRY glEnable (GLenum cap);
GL_APICALL void GL_APIENTRY glEnableVertexAttribArray (GLuint index);
GL_APICALL void GL_APIENTRY glFinish (void);
GL_APICALL void GL_APIENTRY glGenBuffers (GLsizei n, GLuint *buffers);
GL_APICALL void GL_APIENTRY glGenerateMipmap (GLenum target);
GL_APICALL void GL_APIENTRY glGenTextures (GLsizei n, GLuint *textures);
GL_APICALL GLenum GL_APIENTRY glGetError (void);
GL_APICALL void GL_APIENTRY glGetIntegerv (GLenum pname, GLint *data);
GL_APICALL void GL_APIENTRY glGetProgramiv (GLuint program, GLenum pname, GLint *params);
GL_APICALL void GL_APIENTRY glGetProgramInfoLog (GLuint program, GLsizei bufSize, GLsizei *length, GLchar *infoLog);
GL_APICALL void GL_APIENTRY glGetShaderiv (GLuint shader, GLenum pname, GLint *params);
GL_APICALL void GL_APIENTRY glGetShaderInfoLog (GLuint shader, GLsizei bufSize, GLsizei *length, GLchar *infoLog);
GL_APICALL GLint GL_APIENTRY glGetUniformLocation (GLuint program, const GLchar *name);
GL_APICALL void GL_APIENTRY glHint (GLenum target, GLenum mode);
GL_APICALL void GL_APIENTRY glLineWidth (GLfloat width);
GL_APICALL void GL_APIENTRY glLinkProgram (GLuint program);
GL_APICALL void GL_APIENTRY glPixelStorei (GLenum pname, GLint param);
GL_APICALL void GL_APIENTRY glReadPixels (GLint x, GLint y, GLsizei width, GLsizei height, GLenum format, GLenum type, void *pixels);
GL_APICALL void GL_APIENTRY glScissor (GLint x, GLint y, GLsizei width, GLsizei height);
GL_APICALL void GL_APIENTRY glShaderSource (GLuint shader, GLsizei count, const GLchar *const*string, const GLint *length);
GL_APICALL void GL_APIENTRY glTexImage2D (GLenum target, GLint level, GLint internalformat, GLsizei width, GLsizei height, GLint border, GLenum format, GLenum type, const void *pixels);
GL_APICALL void GL_APIENTRY glTexParameteri (GLenum target, GLenum pname, GLint param);
GL_APICALL void GL_APIENTRY glUniform1i (GLint location, GLint v0);
GL_APICALL void GL_APIENTRY glUniform2f (GLint location, GLfloat v0, GLfloat v1);
GL_APICALL void GL_APIENTRY glUniform3f (GLint location, GLfloat v0, GLfloat v1, GLfloat v2);
GL_APICALL void GL_APIENTRY glUniform4f (GLint location, GLfloat v0, GLfloat v1, GLfloat v2, GLfloat v3);
GL_APICALL void GL_APIENTRY glUniformMatrix4fv (GLint location, GLsizei count, GLboolean transpose, const GLfloat *value);
GL_APICALL void GL_APIENTRY glUseProgram (GLuint program);
GL_APICALL void GL_APIENTRY glVertexAttrib4f (GLuint index, GLfloat x, GLfloat y, GLfloat z, GLfloat w);
GL_APICALL void GL_APIENTRY glVertexAttribPointer (GLuint index, GLint size, GLenum type, GLboolean normalized, GLsizei stride, const void *pointer);
GL_APICALL void GL_APIENTRY glViewport (GLint x, GLint y, GLsizei width, GLsizei height);

#ifdef __cplusplus
}
#endif
