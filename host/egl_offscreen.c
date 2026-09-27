/*
 * Offscreen GLES2 context via EGL (Mesa surfaceless platform + pbuffer) and a
 * PNG framebuffer dump. Host test/tool code only; see egl_offscreen.h.
 */
#include "egl_offscreen.h"

#include <EGL/egl.h>
#include <EGL/eglext.h>
#include <GLES2/gl2.h>

#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#ifdef SM_HOST_HAVE_PNG
#include <png.h>
#endif

static struct {
    EGLDisplay dpy;
    EGLSurface surf;
    EGLContext ctx;
    int width, height, depth_bits;
} g_egl = { EGL_NO_DISPLAY, EGL_NO_SURFACE, EGL_NO_CONTEXT, 0, 0, 0 };

/* Whole-token search in a space-separated EGL extension string. */
static int has_extension(const char *list, const char *name)
{
    if (list == NULL) {
        return 0;
    }
    const size_t n = strlen(name);
    const char *p = list;
    while ((p = strstr(p, name)) != NULL) {
        const int starts = (p == list) || (p[-1] == ' ');
        const int ends = (p[n] == '\0') || (p[n] == ' ');
        if (starts && ends) {
            return 1;
        }
        p += n;
    }
    return 0;
}

static EGLDisplay open_display(void)
{
    const char *client_ext = eglQueryString(EGL_NO_DISPLAY, EGL_EXTENSIONS);
    if (has_extension(client_ext, "EGL_EXT_platform_base") &&
        has_extension(client_ext, "EGL_MESA_platform_surfaceless")) {
        PFNEGLGETPLATFORMDISPLAYEXTPROC get_platform_display =
            (PFNEGLGETPLATFORMDISPLAYEXTPROC)eglGetProcAddress("eglGetPlatformDisplayEXT");
        if (get_platform_display != NULL) {
            EGLDisplay d = get_platform_display(EGL_PLATFORM_SURFACELESS_MESA, EGL_DEFAULT_DISPLAY, NULL);
            if (d != EGL_NO_DISPLAY) {
                return d;
            }
        }
    }
    fprintf(stderr, "sm_host_egl: surfaceless platform unavailable; trying the default display\n");
    return eglGetDisplay(EGL_DEFAULT_DISPLAY);
}

static int attrib(EGLConfig c, EGLint name)
{
    EGLint v = 0;
    eglGetConfigAttrib(g_egl.dpy, c, name, &v);
    return (int)v;
}

static int choose_config(int depth_bits, EGLConfig *out)
{
    const EGLint want[] = {
        EGL_SURFACE_TYPE, EGL_PBUFFER_BIT,
        EGL_RENDERABLE_TYPE, EGL_OPENGL_ES2_BIT,
        EGL_RED_SIZE, 8, EGL_GREEN_SIZE, 8, EGL_BLUE_SIZE, 8, EGL_ALPHA_SIZE, 8,
        EGL_DEPTH_SIZE, depth_bits,
        EGL_NONE,
    };
    EGLConfig configs[128];
    EGLint n = 0;
    if (!eglChooseConfig(g_egl.dpy, want, configs, 128, &n) || n <= 0) {
        return -1;
    }
    /* EGL sorts deeper colour first and smaller depth first; insist on an
     * exact RGBA8888, single-sampled config and prefer the exact depth. */
    int fallback = -1;
    for (EGLint i = 0; i < n; ++i) {
        EGLConfig c = configs[i];
        if (attrib(c, EGL_RED_SIZE) != 8 || attrib(c, EGL_GREEN_SIZE) != 8 ||
            attrib(c, EGL_BLUE_SIZE) != 8 || attrib(c, EGL_ALPHA_SIZE) != 8 ||
            attrib(c, EGL_SAMPLE_BUFFERS) != 0) {
            continue;
        }
        if (attrib(c, EGL_DEPTH_SIZE) == depth_bits) {
            *out = c;
            return 0;
        }
        if (fallback < 0) {
            fallback = (int)i;
        }
    }
    if (fallback < 0) {
        return -1;
    }
    *out = configs[fallback];
    return 0;
}

int sm_host_egl_create(int width, int height, int depth_bits)
{
    if (g_egl.dpy != EGL_NO_DISPLAY) {
        sm_host_egl_destroy();
    }
    if (width <= 0 || height <= 0) {
        return -1;
    }
    g_egl.dpy = open_display();
    if (g_egl.dpy == EGL_NO_DISPLAY) {
        fprintf(stderr, "sm_host_egl: no EGL display\n");
        return -1;
    }
    EGLint major = 0, minor = 0;
    if (!eglInitialize(g_egl.dpy, &major, &minor)) {
        fprintf(stderr, "sm_host_egl: eglInitialize failed (0x%04x)\n", (unsigned)eglGetError());
        g_egl.dpy = EGL_NO_DISPLAY;
        eglReleaseThread();
        return -1;
    }
    EGLConfig cfg;
    if (!eglBindAPI(EGL_OPENGL_ES_API) || choose_config(depth_bits, &cfg) != 0) {
        fprintf(stderr, "sm_host_egl: no RGBA8888 pbuffer config with ES2 and depth >= %d\n", depth_bits);
        sm_host_egl_destroy();
        return -1;
    }
    const EGLint pb_attr[] = { EGL_WIDTH, width, EGL_HEIGHT, height, EGL_NONE };
    g_egl.surf = eglCreatePbufferSurface(g_egl.dpy, cfg, pb_attr);
    if (g_egl.surf == EGL_NO_SURFACE) {
        fprintf(stderr, "sm_host_egl: eglCreatePbufferSurface failed (0x%04x)\n", (unsigned)eglGetError());
        sm_host_egl_destroy();
        return -1;
    }
    const EGLint ctx_attr[] = { EGL_CONTEXT_CLIENT_VERSION, 2, EGL_NONE };
    g_egl.ctx = eglCreateContext(g_egl.dpy, cfg, EGL_NO_CONTEXT, ctx_attr);
    if (g_egl.ctx == EGL_NO_CONTEXT) {
        fprintf(stderr, "sm_host_egl: eglCreateContext(ES2) failed (0x%04x)\n", (unsigned)eglGetError());
        sm_host_egl_destroy();
        return -1;
    }
    if (!eglMakeCurrent(g_egl.dpy, g_egl.surf, g_egl.surf, g_egl.ctx)) {
        fprintf(stderr, "sm_host_egl: eglMakeCurrent failed (0x%04x)\n", (unsigned)eglGetError());
        sm_host_egl_destroy();
        return -1;
    }
    g_egl.width = width;
    g_egl.height = height;
    g_egl.depth_bits = attrib(cfg, EGL_DEPTH_SIZE);
    return 0;
}

void sm_host_egl_destroy(void)
{
    if (g_egl.dpy != EGL_NO_DISPLAY) {
        eglMakeCurrent(g_egl.dpy, EGL_NO_SURFACE, EGL_NO_SURFACE, EGL_NO_CONTEXT);
        if (g_egl.ctx != EGL_NO_CONTEXT) {
            eglDestroyContext(g_egl.dpy, g_egl.ctx);
        }
        if (g_egl.surf != EGL_NO_SURFACE) {
            eglDestroySurface(g_egl.dpy, g_egl.surf);
        }
        eglTerminate(g_egl.dpy);
        eglReleaseThread();
    }
    g_egl.dpy = EGL_NO_DISPLAY;
    g_egl.surf = EGL_NO_SURFACE;
    g_egl.ctx = EGL_NO_CONTEXT;
    g_egl.width = g_egl.height = g_egl.depth_bits = 0;
}

int sm_host_egl_width(void) { return g_egl.width; }
int sm_host_egl_height(void) { return g_egl.height; }
int sm_host_egl_depth_bits(void) { return g_egl.depth_bits; }

int sm_host_read_rgba_topdown(unsigned char *dst)
{
    if (g_egl.ctx == EGL_NO_CONTEXT || dst == NULL) {
        return -1;
    }
    const size_t row = (size_t)g_egl.width * 4u;
    unsigned char *tmp = malloc(row * (size_t)g_egl.height);
    if (tmp == NULL) {
        return -1;
    }
    GLint old_align = 4;
    glGetIntegerv(GL_PACK_ALIGNMENT, &old_align);
    glPixelStorei(GL_PACK_ALIGNMENT, 1);
    glReadPixels(0, 0, g_egl.width, g_egl.height, GL_RGBA, GL_UNSIGNED_BYTE, tmp);
    glPixelStorei(GL_PACK_ALIGNMENT, old_align);
    const GLenum err = glGetError();
    if (err == GL_NO_ERROR) {
        /* GL rows are bottom-up; flip so row 0 is the top of the image. */
        for (int y = 0; y < g_egl.height; ++y) {
            memcpy(dst + (size_t)y * row, tmp + (size_t)(g_egl.height - 1 - y) * row, row);
        }
    } else {
        fprintf(stderr, "sm_host_egl: glReadPixels failed (0x%04x)\n", (unsigned)err);
    }
    free(tmp);
    return err == GL_NO_ERROR ? 0 : -1;
}

int sm_host_png_supported(void)
{
#ifdef SM_HOST_HAVE_PNG
    return 1;
#else
    return 0;
#endif
}

int sm_host_write_png(const char *path)
{
#ifdef SM_HOST_HAVE_PNG
    if (path == NULL || g_egl.ctx == EGL_NO_CONTEXT) {
        return -1;
    }
    const size_t row = (size_t)g_egl.width * 4u;
    unsigned char *pixels = malloc(row * (size_t)g_egl.height);
    if (pixels == NULL) {
        return -1;
    }
    if (sm_host_read_rgba_topdown(pixels) != 0) {
        free(pixels);
        return -1;
    }
    png_image img;
    memset(&img, 0, sizeof img);
    img.version = PNG_IMAGE_VERSION;
    img.width = (png_uint_32)g_egl.width;
    img.height = (png_uint_32)g_egl.height;
    img.format = PNG_FORMAT_RGBA;
    const int ok = png_image_write_to_file(&img, path, 0, pixels, (png_int_32)row, NULL);
    if (!ok) {
        fprintf(stderr, "sm_host_egl: writing %s failed: %s\n", path, img.message);
    }
    png_image_free(&img);
    free(pixels);
    return ok ? 0 : -1;
#else
    (void)path;
    fprintf(stderr, "sm_host_egl: built without libpng; cannot write PNG\n");
    return -1;
#endif
}
