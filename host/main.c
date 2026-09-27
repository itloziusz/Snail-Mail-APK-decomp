/*
 * snailmail_host: runs the ahead-of-time translated Snail Mail game code on a
 * Linux host, driving it exactly as the original Android shell does
 * (docs/BOOT_CHAIN.md "Managed side"):
 *   Activity.onCreate  -> JNIDatInit(FileDescriptor(asm.mp3), start, length)
 *   onSurfaceCreated   -> nativeInit()
 *   onSurfaceChanged   -> nativeResize(w, h)
 *   onDrawFrame        -> nativeRender(hasFocus ? 0 : 1), every frame
 *   input              -> JNIMouseEvent / JNIKey / JNIAccelerometer
 * Rendering: GLES 1.1 emulation on Mesa GLES 2 offscreen (PNG capture), or a
 * headless backend. Time: virtual 60 Hz clock by default (deterministic).
 *
 * Input script lines:  <frame> down|move|up <x> <y>   |   <frame> key <code>
 *                      <frame> accel <x> <y> <z>      |   <frame> shot
 * Development tool; never shipped.
 */
#define _GNU_SOURCE
#include <errno.h>
#include <fcntl.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/stat.h>
#include <time.h>
#include <unistd.h>

#include "aot_host.h"
#include "java_emul.h"

#ifdef SM_HOST_HAVE_GL
#include "egl_offscreen.h"
#include "sm_rendering/smgl.h"
#endif

const sm_gl_backend *sm_null_gl_backend(void);
void aot_install_fault_handler(void);
uint64_t aot_gl_call_count(void);

typedef struct {
    int frame;
    char what[8];
    float x, y, z;
    int key;
} event;

static event *g_events;
static int g_nevents;

static void load_script(const char *path)
{
    FILE *f = fopen(path, "r");
    char line[256];
    if (!f) {
        fprintf(stderr, "cannot open input script %s\n", path);
        exit(2);
    }
    while (fgets(line, sizeof line, f)) {
        event e;
        memset(&e, 0, sizeof e);
        if (line[0] == '#' || sscanf(line, "%d %7s", &e.frame, e.what) != 2) continue;
        if (!strcmp(e.what, "key")) sscanf(line, "%*d %*s %d", &e.key);
        else if (!strcmp(e.what, "accel")) sscanf(line, "%*d %*s %f %f %f", &e.x, &e.y, &e.z);
        else sscanf(line, "%*d %*s %f %f", &e.x, &e.y);
        g_events = (event *)realloc(g_events, sizeof(event) * (size_t)(g_nevents + 1));
        g_events[g_nevents++] = e;
    }
    fclose(f);
}

static void host_log(int level, const char *msg)
{
    static const char *lv[] = {"", "", "", "D", "I", "W", "E", "F"};
    if (level >= 4 || getenv("SM_HOST_DEBUG")) {
        fprintf(stderr, "[%s] %s\n", lv[level & 7], msg);
    }
}

static aot_cpu *g_cpu;
static uint32_t g_env;

static uint32_t call(const char *sym, uint32_t *args, int n)
{
    uint32_t addr = aot_symbol_addr(sym);
    if (!addr) {
        fprintf(stderr, "unknown guest symbol %s\n", sym);
        exit(3);
    }
    return aot_invoke(g_cpu, addr, args, n, NULL);
}

static uint32_t fbits(float f)
{
    uint32_t u;
    memcpy(&u, &f, 4);
    return u;
}

int sm_host_run(int argc, char **argv)
{
    const char *assets = "work/apk_unzip/assets", *files = NULL, *out = NULL, *script = NULL;
    const char *trace = NULL, *gltrace = NULL;
    int frames = 300, width = 800, height = 480, shot_every = 0, headless = 0, realtime = 0;
    double hz = 60.0; /* simulated display refresh (virtual clock step per frame) */
    int i, fd;
    struct stat st;
    char asm_path[1024], tmpl[] = "/tmp/snailmail_filesXXXXXX";
    FILE *trace_f = NULL, *gltrace_f = NULL;
    sm_java_emul_cfg jcfg;
    aot_config cfg;
    const sm_gl_backend *gl;
    uint32_t act_cls, view_cls, acc_obj, thiz;
    struct timespec t0, t1;

    for (i = 1; i < argc; ++i) {
        const char *a = argv[i];
        const char *v = i + 1 < argc ? argv[i + 1] : NULL;
        if (!strcmp(a, "--assets") && v) assets = argv[++i];
        else if (!strcmp(a, "--files") && v) files = argv[++i];
        else if (!strcmp(a, "--out") && v) out = argv[++i];
        else if (!strcmp(a, "--frames") && v) frames = atoi(argv[++i]);
        else if (!strcmp(a, "--size") && v) sscanf(argv[++i], "%dx%d", &width, &height);
        else if (!strcmp(a, "--shot-every") && v) shot_every = atoi(argv[++i]);
        else if (!strcmp(a, "--input") && v) script = argv[++i];
        else if (!strcmp(a, "--trace") && v) trace = argv[++i];
        else if (!strcmp(a, "--gltrace") && v) gltrace = argv[++i];
        else if (!strcmp(a, "--headless")) headless = 1;
        else if (!strcmp(a, "--realtime")) realtime = 1;
        else if (!strcmp(a, "--hz") && v) hz = atof(argv[++i]);
        else {
            fprintf(stderr,
                    "usage: %s [--assets DIR] [--files DIR] [--out DIR] [--frames N] [--size WxH]\n"
                    "          [--shot-every K] [--input SCRIPT] [--trace F] [--gltrace F] [--headless] [--realtime] [--hz N]\n",
                    argv[0]);
            return 2;
        }
    }
    if (!files) {
        files = mkdtemp(tmpl);
        if (!files) {
            perror("mkdtemp");
            return 1;
        }
    }
    if (out) mkdir(out, 0755);
    if (script) load_script(script);
    if (trace) trace_f = fopen(trace, "w");
    if (gltrace) gltrace_f = fopen(gltrace, "w");

#ifdef SM_HOST_HAVE_GL
    if (!headless) {
        if (sm_host_egl_create(width, height, 16) != 0) {
            fprintf(stderr, "EGL offscreen context unavailable; use --headless\n");
            return 1;
        }
        if (smgl_init() != 0) {
            fprintf(stderr, "smgl_init failed\n");
            return 1;
        }
        gl = smgl_backend();
    } else {
        gl = sm_null_gl_backend();
    }
#else
    headless = 1;
    gl = sm_null_gl_backend();
#endif

    memset(&jcfg, 0, sizeof jcfg);
    jcfg.assets_dir = assets;
    jcfg.files_dir = files;
    jcfg.virtual_clock = !realtime;
    jcfg.verbose = getenv("SM_HOST_DEBUG") != NULL;

    memset(&cfg, 0, sizeof cfg);
    cfg.java = sm_java_emul_ops(&jcfg);
    cfg.gl = gl;
    cfg.files_dir = files;
    cfg.log = host_log;
    cfg.trace = trace_f;
    aot_install_fault_handler();
    aot_gl_trace_set(gltrace_f);
    clock_gettime(CLOCK_MONOTONIC, &t0);
    if (aot_init(&cfg) != 0) return 1;

    g_cpu = aot_thread_enter(NULL);
    g_env = aot_thread_guest_env();
    act_cls = aot_handle_for_incoming(NULL, sm_java_emul_class("com/sandlotgames/snailmail/SnailMailActivity"));
    view_cls = aot_handle_for_incoming(NULL, sm_java_emul_class("com/sandlotgames/snailmail/ADGLSurfaceView"));
    thiz = aot_handle_for_incoming(NULL, sm_java_emul_renderer());
    acc_obj = aot_handle_for_incoming(NULL, sm_java_emul_class("com/sandlotgames/snailmail/AccelerometerListener"));

    /* Activity.onCreate: JNIDatInit(fd of asm.mp3, start, length) */
    snprintf(asm_path, sizeof asm_path, "%s/asm.mp3", assets);
    fd = open(asm_path, O_RDONLY);
    if (fd < 0 || fstat(fd, &st) != 0) {
        fprintf(stderr, "cannot open %s: %s\n", asm_path, strerror(errno));
        return 1;
    }
    {
        uint32_t fdh = aot_handle_for_incoming(NULL, sm_java_emul_file_descriptor(fd));
        uint32_t a[5] = {g_env, act_cls, fdh, 0u, (uint32_t)st.st_size};
        call("Java_com_sandlotgames_snailmail_SnailMailActivity_JNIDatInit", a, 5);
    }
    /* GL thread */
    {
        uint32_t a[2] = {g_env, thiz};
        call("Java_com_sandlotgames_snailmail_ADRenderer_nativeInit", a, 2);
    }
    {
        uint32_t a[4] = {g_env, thiz, (uint32_t)width, (uint32_t)height};
        call("Java_com_sandlotgames_snailmail_ADRenderer_nativeResize", a, 4);
    }
    fprintf(stderr, "[host] boot complete; rendering %d frames at %dx%d (%s)\n", frames, width, height,
            headless ? "headless" : "GLES1-on-GLES2 offscreen");

    for (int frame = 0; frame < frames; ++frame) {
        int shot = shot_every > 0 && (frame + 1) % shot_every == 0;
        (void)shot;
        for (int k = 0; k < g_nevents; ++k) {
            event *e = &g_events[k];
            if (e->frame != frame) continue;
            if (!strcmp(e->what, "down") || !strcmp(e->what, "move") || !strcmp(e->what, "up")) {
                uint32_t action = !strcmp(e->what, "down") ? 0u : !strcmp(e->what, "move") ? 1u : 2u;
                uint32_t a[5] = {g_env, view_cls, action, fbits(e->x), fbits(e->y)};
                call("Java_com_sandlotgames_snailmail_ADGLSurfaceView_JNIMouseEvent", a, 5);
            } else if (!strcmp(e->what, "key")) {
                uint32_t a[3] = {g_env, thiz, (uint32_t)e->key};
                call("Java_com_sandlotgames_snailmail_ADGLSurfaceView_JNIKey", a, 3);
            } else if (!strcmp(e->what, "accel")) {
                uint32_t a[5] = {g_env, acc_obj, fbits(e->x), fbits(e->y), fbits(e->z)};
                call("Java_com_sandlotgames_snailmail_AccelerometerListener_JNIAccelerometer", a, 5);
            } else if (!strcmp(e->what, "shot")) {
                shot = 1;
            }
        }
        {
            uint32_t a[3] = {g_env, thiz, 0u};
            call("Java_com_sandlotgames_snailmail_ADRenderer_nativeRender", a, 3);
        }
        if (!realtime) sm_java_clock_advance((uint64_t)(1e9 / hz + 0.5));
#ifdef SM_HOST_HAVE_GL
        if (shot && out && !headless) {
            char p[1200];
            snprintf(p, sizeof p, "%s/frame_%05d.png", out, frame + 1);
            if (sm_host_write_png(p) == 0) fprintf(stderr, "[host] wrote %s\n", p);
        }
#endif
        if (trace_f) fflush(trace_f);
    }
    clock_gettime(CLOCK_MONOTONIC, &t1);
    {
        const sm_audio_stats *au = sm_java_emul_audio_stats();
        double secs = (double)(t1.tv_sec - t0.tv_sec) + (double)(t1.tv_nsec - t0.tv_nsec) / 1e9;
        fprintf(stderr,
                "[host] done: %d frames in %.2f s wall (%.1f fps); GL calls %llu; samples loaded %d "
                "(missing %d), sample plays %d, music starts %d (last '%s')\n",
                frames, secs, frames / secs, (unsigned long long)aot_gl_call_count(), au->samples_loaded,
                au->samples_missing, au->sample_plays, au->music_starts, au->last_music);
    }
    if (trace_f) fclose(trace_f);
    if (gltrace_f) fclose(gltrace_f);
    return 0;
}
