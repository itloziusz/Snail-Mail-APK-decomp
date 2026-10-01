#import <Foundation/Foundation.h>

#include <fcntl.h>
#include <limits.h>
#include <stdio.h>
#include <string.h>
#include <sys/stat.h>
#include <unistd.h>

#include "SMIOSHost.h"
#include "SMIOSJava.h"
#include "aot_host.h"
#include "port.h"
#include "sm_rendering/smgl.h"

static aot_cpu *g_cpu;
static uint32_t g_env;
static uint32_t g_activity_class;
static uint32_t g_view_class;
static uint32_t g_accel_object;
static uint32_t g_renderer;
static int g_booted;
static char g_error[512];

static void ios_log(int level, const char *msg) {
    if (level >= 4) NSLog(@"[SnailMail/AOT:%d] %s", level, msg ? msg : "");
}

static void fail(const char *msg) {
    snprintf(g_error, sizeof g_error, "%s", msg ? msg : "unknown iOS host error");
    NSLog(@"[SnailMail/iOS] %s", g_error);
}

static uint32_t fbits(float f) {
    uint32_t u;
    memcpy(&u, &f, sizeof u);
    return u;
}

static uint32_t call_guest(const char *symbol, uint32_t *args, int nargs) {
    uint32_t addr = aot_symbol_addr(symbol);
    if (!addr) {
        char b[384];
        snprintf(b, sizeof b, "missing translated symbol %s", symbol);
        fail(b);
        return 0;
    }
    return aot_invoke(g_cpu, addr, args, nargs, NULL);
}

const char *sm_ios_host_last_error(void) {
    return g_error[0] ? g_error : NULL;
}

int sm_ios_host_boot(const char *assets_dir, const char *files_dir, int width, int height) {
    if (g_booted) {
        sm_ios_host_resize(width, height);
        return 0;
    }
    if (!assets_dir || !files_dir || width <= 0 || height <= 0) {
        fail("invalid iOS host bootstrap arguments");
        return -1;
    }

    sm_ios_java_config jcfg = {assets_dir, files_dir, 0};
    aot_config cfg;
    memset(&cfg, 0, sizeof cfg);
    cfg.java = sm_ios_java_ops(&jcfg);
    cfg.gl = smgl_backend();
    cfg.files_dir = files_dir;
    cfg.log = ios_log;

    if (aot_init(&cfg) != 0) {
        fail("aot_init failed");
        return -1;
    }

    sm_port_settings port;
    sm_port_settings_default(&port);
    port.refresh = SM_PORT_REFRESH_60;
    sm_port_set_refresh_modes(1u << SM_PORT_REFRESH_60);
    sm_port_set(&port);

    g_cpu = aot_thread_enter(NULL);
    g_env = aot_thread_guest_env();
    g_activity_class = aot_handle_for_incoming(NULL, sm_ios_java_class("com/sandlotgames/snailmail/SnailMailActivity"));
    g_view_class = aot_handle_for_incoming(NULL, sm_ios_java_class("com/sandlotgames/snailmail/ADGLSurfaceView"));
    g_accel_object = aot_handle_for_incoming(NULL, sm_ios_java_class("com/sandlotgames/snailmail/AccelerometerListener"));
    g_renderer = aot_handle_for_incoming(NULL, sm_ios_java_renderer());

    char asm_path[PATH_MAX];
    snprintf(asm_path, sizeof asm_path, "%s/asm.mp3", assets_dir);
    int fd = open(asm_path, O_RDONLY);
    struct stat st;
    if (fd < 0 || fstat(fd, &st) != 0 || st.st_size <= 0 || (uint64_t)st.st_size > 0xffffffffu) {
        if (fd >= 0) close(fd);
        fail("cannot open bundled assets/asm.mp3");
        return -1;
    }

    uint32_t fdh = aot_handle_for_incoming(NULL, sm_ios_java_file_descriptor(fd));
    uint32_t dat_args[5] = {g_env, g_activity_class, fdh, 0u, (uint32_t)st.st_size};
    call_guest("Java_com_sandlotgames_snailmail_SnailMailActivity_JNIDatInit", dat_args, 5);
    close(fd);
    if (g_error[0]) return -1;

    uint32_t init_args[2] = {g_env, g_renderer};
    call_guest("Java_com_sandlotgames_snailmail_ADRenderer_nativeInit", init_args, 2);
    if (g_error[0]) return -1;

    uint32_t resize_args[4] = {g_env, g_renderer, (uint32_t)width, (uint32_t)height};
    call_guest("Java_com_sandlotgames_snailmail_ADRenderer_nativeResize", resize_args, 4);
    if (g_error[0]) return -1;

    g_booted = 1;
    NSLog(@"[SnailMail/iOS] native bootstrap complete (%dx%d)", width, height);
    return 0;
}

void sm_ios_host_resize(int width, int height) {
    if (!g_booted || width <= 0 || height <= 0) return;
    uint32_t a[4] = {g_env, g_renderer, (uint32_t)width, (uint32_t)height};
    call_guest("Java_com_sandlotgames_snailmail_ADRenderer_nativeResize", a, 4);
}

void sm_ios_host_render(int paused) {
    if (!g_booted) return;
    sm_port_frame_begin();
    uint32_t a[3] = {g_env, g_renderer, paused ? 1u : 0u};
    call_guest("Java_com_sandlotgames_snailmail_ADRenderer_nativeRender", a, 3);
}

void sm_ios_host_touch(int action, float x, float y) {
    if (!g_booted) return;
    sm_port_map_touch(&x, &y);
    uint32_t a[5] = {g_env, g_view_class, (uint32_t)action, fbits(x), fbits(y)};
    call_guest("Java_com_sandlotgames_snailmail_ADGLSurfaceView_JNIMouseEvent", a, 5);
}

void sm_ios_host_accelerometer(float x, float y, float z) {
    if (!g_booted) return;
    uint32_t a[5] = {g_env, g_accel_object, fbits(x), fbits(y), fbits(z)};
    call_guest("Java_com_sandlotgames_snailmail_AccelerometerListener_JNIAccelerometer", a, 5);
}
