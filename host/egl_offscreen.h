/*
 * Host-only (never shipped) offscreen GLES2 context for tests and tools:
 * EGL on Mesa's surfaceless platform with a W x H pbuffer, plus a PNG dump of
 * the framebuffer. Used by tests/unit/rendering to exercise sm_rendering
 * (docs/RENDERING.md §6). One context per process.
 */
#ifndef SM_HOST_EGL_OFFSCREEN_H
#define SM_HOST_EGL_OFFSCREEN_H

#ifdef __cplusplus
extern "C" {
#endif

/* Create an EGL display (EGL_PLATFORM_SURFACELESS_MESA, falling back to the
 * default display), an RGBA8888 pbuffer of width x height with a depth buffer
 * of `depth_bits` (16 or 24; the smallest config with at least that many
 * bits is used), a GLES 2 context, and make them current.
 * Returns 0 on success, -1 on failure (reason on stderr; nothing leaks). */
int sm_host_egl_create(int width, int height, int depth_bits);

/* Release the context, surface and display. Safe to call when not created. */
void sm_host_egl_destroy(void);

/* Size and depth bits of the current pbuffer (0 when not created). */
int sm_host_egl_width(void);
int sm_host_egl_height(void);
int sm_host_egl_depth_bits(void);

/* Read the whole pbuffer (glReadPixels GL_RGBA/GL_UNSIGNED_BYTE) into `dst`
 * (width*height*4 bytes) in top-down row order. Returns 0 or -1. */
int sm_host_read_rgba_topdown(unsigned char *dst);

/* Read the framebuffer and write it as an 8-bit RGBA PNG, top row first.
 * Returns 0 on success, -1 on failure (including builds without libpng). */
int sm_host_write_png(const char *path);

/* 1 when this build can write PNGs (libpng was found), else 0. */
int sm_host_png_supported(void);

#ifdef __cplusplus
}
#endif
#endif /* SM_HOST_EGL_OFFSCREEN_H */
