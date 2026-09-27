/*
 * Host emulation of the Java callbacks of the original Snail Mail shell.
 * Each method mirrors ADRenderer.java (work/jadx; verified against smali in
 * docs/APK_AUDIT.md §9). Audio is not played on the host: sample/music
 * requests are validated (the .ogg must exist, as openFd would require) and
 * counted, returning the IDs SoundPool/MediaPlayer would.
 */
#define _GNU_SOURCE
#include "java_emul.h"

#include <jpeglib.h>
#include <png.h>
#include <setjmp.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/stat.h>
#include <time.h>
#include <zlib.h>

enum kind { K_CLASS = 1, K_RENDERER, K_FD, K_STRING, K_BYTES };

typedef struct jobj {
    enum kind kind;
    char name[128];     /* class name or string contents (strings may be longer: see str) */
    char *str;
    int32_t len;        /* byte array length */
    uint8_t *data;
    int fd;
    struct jobj *cls;
} jobj;

typedef struct {
    const char *name;
    const char *sig;
} jmethod;

static const jmethod k_methods[] = {
    {"JAVALoadSample", "(Ljava/lang/String;)I"},  {"JAVAPlaySample", "(IF)I"},
    {"JAVAStopSample", "(I)V"},                   {"JAVASaveFile", "(Ljava/lang/String;[BI)I"},
    {"JAVADeleteFile", "(Ljava/lang/String;)V"},  {"JAVALoadFile", "(Ljava/lang/String;[BI)V"},
    {"JAVAFindFile", "(Ljava/lang/String;)I"},    {"JAVAFileSize", "(Ljava/lang/String;)I"},
    {"JAVASetMusicVolume", "(F)V"},               {"JAVAPlayMusic", "(Ljava/lang/String;)V"},
    {"JAVAStopMusic", "()V"},                     {"JAVAUnPauseMusic", "()V"},
    {"JAVAPauseMusic", "()V"},                    {"JAVAMusicRestart", "()V"},
    {"JAVAUnZip", "([B[B)V"},                     {"JAVAUnJpg", "([B[B)V"},
    {"JAVAUnPng", "([B[B)V"},                     {"JAVAOpenFeintOpen", "()V"},
    {"JAVAOpenFeintLastLoggedInUserID", "([B)V"}, {"JAVAOpenFeintSubmit", "(Ljava/lang/String;II)V"},
    {"JAVAOpenFeintUnlock", "(Ljava/lang/String;I)V"}, {"JAVAOpenFeintIsUserLoggedIn", "()I"},
    {"JAVAOpenFeintIsOnline", "()I"},             {"JAVATime", "()I"},
    {"JAVATimeHi", "()I"},                        {"JAVAVibrate", "(I)V"},
};
static const char k_fd_field[] = "descriptor";

static sm_java_emul_cfg g_cfg;
static jobj g_classes[16];
static int g_nclasses;
static jobj g_renderer;
static uint64_t g_clock_ns = 1000000000ull; /* virtual nanoTime origin (1 s) */
static int64_t g_jtime;                     /* ADRenderer.JTime */
static int g_next_sample = 1, g_next_stream = 1;
static sm_audio_stats g_audio;

void sm_java_clock_advance(uint64_t ns) { g_clock_ns += ns; }
uint64_t sm_java_clock_now(void)
{
    if (g_cfg.virtual_clock) return g_clock_ns;
    struct timespec ts;
    clock_gettime(CLOCK_MONOTONIC, &ts);
    return (uint64_t)ts.tv_sec * 1000000000ull + (uint64_t)ts.tv_nsec;
}
const sm_audio_stats *sm_java_emul_audio_stats(void) { return &g_audio; }

void *sm_java_emul_class(const char *name)
{
    int i;
    for (i = 0; i < g_nclasses; ++i) {
        if (strcmp(g_classes[i].name, name) == 0) return &g_classes[i];
    }
    if (strcmp(name, "java/io/FileDescriptor") && strcmp(name, "com/sandlotgames/snailmail/ADRenderer") &&
        strncmp(name, "com/sandlotgames/snailmail/", 27) && strcmp(name, "java/lang/String")) {
        fprintf(stderr, "[java] FindClass(%s): NoClassDefFoundError\n", name);
        return NULL;
    }
    if (g_nclasses >= 16) return NULL;
    g_classes[g_nclasses].kind = K_CLASS;
    snprintf(g_classes[g_nclasses].name, sizeof g_classes[0].name, "%s", name);
    return &g_classes[g_nclasses++];
}

void *sm_java_emul_renderer(void)
{
    if (!g_renderer.kind) {
        g_renderer.kind = K_RENDERER;
        g_renderer.cls = (jobj *)sm_java_emul_class("com/sandlotgames/snailmail/ADRenderer");
    }
    return &g_renderer;
}

void *sm_java_emul_file_descriptor(int fd)
{
    jobj *o = (jobj *)calloc(1, sizeof *o);
    o->kind = K_FD;
    o->fd = fd;
    o->cls = (jobj *)sm_java_emul_class("java/io/FileDescriptor");
    return o;
}

static jobj *new_bytes(int32_t len)
{
    jobj *o = (jobj *)calloc(1, sizeof *o);
    o->kind = K_BYTES;
    o->len = len;
    o->data = (uint8_t *)calloc((size_t)(len > 0 ? len : 1), 1);
    return o;
}

/* ---------------------------------------------------------------- ops */
static void *op_find_class(void *ctx, const char *name) { (void)ctx; return sm_java_emul_class(name); }
static void *op_get_object_class(void *ctx, void *obj) { (void)ctx; return obj ? ((jobj *)obj)->cls : NULL; }
static void *op_new_global_ref(void *ctx, void *obj) { (void)ctx; return obj; }
static void op_delete_ref(void *ctx, void *obj)
{
    jobj *o = (jobj *)obj;
    (void)ctx;
    if (!o) return;
    if (o->kind == K_BYTES) {
        free(o->data);
        free(o);
    } else if (o->kind == K_STRING) {
        free(o->str);
        free(o);
    }
    /* classes, renderer, fds are not freed */
}
static int op_is_same_object(void *ctx, void *a, void *b) { (void)ctx; return a == b; }
static void *op_get_method_id(void *ctx, void *cls, const char *name, const char *sig)
{
    size_t i;
    (void)ctx;
    (void)cls;
    for (i = 0; i < sizeof k_methods / sizeof k_methods[0]; ++i) {
        if (strcmp(k_methods[i].name, name) == 0 && strcmp(k_methods[i].sig, sig) == 0) {
            return (void *)&k_methods[i];
        }
    }
    fprintf(stderr, "[java] GetMethodID(%s %s): NoSuchMethodError\n", name, sig);
    return NULL;
}
static void *op_get_field_id(void *ctx, void *cls, const char *name, const char *sig)
{
    (void)ctx;
    if (cls && strcmp(((jobj *)cls)->name, "java/io/FileDescriptor") == 0 && strcmp(name, "descriptor") == 0 &&
        strcmp(sig, "I") == 0) {
        return (void *)k_fd_field;
    }
    fprintf(stderr, "[java] GetFieldID(%s %s): NoSuchFieldError\n", name, sig);
    return NULL;
}
static int32_t op_get_int_field(void *ctx, void *obj, void *fid)
{
    (void)ctx;
    if (fid == (void *)k_fd_field && obj && ((jobj *)obj)->kind == K_FD) return ((jobj *)obj)->fd;
    return 0;
}
static void *op_new_string_utf(void *ctx, const char *s)
{
    jobj *o = (jobj *)calloc(1, sizeof *o);
    (void)ctx;
    o->kind = K_STRING;
    o->str = strdup(s);
    o->cls = (jobj *)sm_java_emul_class("java/lang/String");
    return o;
}
static char *op_get_string_utf_chars(void *ctx, void *str)
{
    (void)ctx;
    return strdup(str ? ((jobj *)str)->str : "");
}
static void *op_new_byte_array(void *ctx, int32_t len) { (void)ctx; return new_bytes(len); }
static void op_get_region(void *ctx, void *arr, int32_t start, int32_t len, void *buf)
{
    jobj *o = (jobj *)arr;
    (void)ctx;
    if (start < 0 || len < 0 || start + len > o->len) {
        fprintf(stderr, "[java] GetByteArrayRegion out of bounds\n");
        abort();
    }
    memcpy(buf, o->data + start, (size_t)len);
}
static void op_set_region(void *ctx, void *arr, int32_t start, int32_t len, const void *buf)
{
    jobj *o = (jobj *)arr;
    (void)ctx;
    if (start < 0 || len < 0 || start + len > o->len) {
        fprintf(stderr, "[java] SetByteArrayRegion out of bounds\n");
        abort();
    }
    memcpy(o->data + start, buf, (size_t)len);
}

/* ---------------------------------------------------------------- decoders */
/* Skia's SkMulDiv255Round: premultiplication used by Android ARGB_8888 bitmaps. */
static uint8_t mul255(unsigned c, unsigned a)
{
    unsigned p = c * a + 128u;
    return (uint8_t)((p + (p >> 8)) >> 8);
}

/* BitmapFactory.decodeByteArray(ARGB_8888) + copyPixelsToBuffer: RGBA bytes,
 * premultiplied. Writes at most out->len bytes (Java would throw otherwise). */
static int decode_png(const uint8_t *in, int32_t n, jobj *out)
{
    png_image img;
    uint8_t *px;
    size_t i, sz;
    memset(&img, 0, sizeof img);
    img.version = PNG_IMAGE_VERSION;
    if (!png_image_begin_read_from_memory(&img, in, (size_t)n)) return -1;
    img.format = PNG_FORMAT_RGBA;
    sz = PNG_IMAGE_SIZE(img);
    px = (uint8_t *)malloc(sz);
    if (!png_image_finish_read(&img, NULL, px, 0, NULL)) {
        free(px);
        return -1;
    }
    for (i = 0; i < sz; i += 4) {
        uint8_t a = px[i + 3];
        px[i] = mul255(px[i], a);
        px[i + 1] = mul255(px[i + 1], a);
        px[i + 2] = mul255(px[i + 2], a);
    }
    if ((int64_t)sz > out->len) {
        fprintf(stderr, "[java] UnPng: %zu bytes > buffer %d (BufferOverflowException)\n", sz, out->len);
        free(px);
        return -1;
    }
    memcpy(out->data, px, sz);
    free(px);
    return 0;
}

struct jerr {
    struct jpeg_error_mgr pub;
    jmp_buf jb;
};
static void jerr_exit(j_common_ptr ci) { longjmp(((struct jerr *)ci->err)->jb, 1); }

static int decode_jpg(const uint8_t *in, int32_t n, jobj *out)
{
    struct jpeg_decompress_struct ci;
    struct jerr je;
    uint8_t *row;
    size_t off = 0;
    ci.err = jpeg_std_error(&je.pub);
    je.pub.error_exit = jerr_exit;
    if (setjmp(je.jb)) {
        jpeg_destroy_decompress(&ci);
        return -1;
    }
    jpeg_create_decompress(&ci);
    jpeg_mem_src(&ci, in, (unsigned long)n);
    jpeg_read_header(&ci, TRUE);
    ci.out_color_space = JCS_RGB;
    jpeg_start_decompress(&ci);
    if ((int64_t)ci.output_width * ci.output_height * 4 > out->len) {
        jpeg_destroy_decompress(&ci);
        return -1;
    }
    row = (uint8_t *)malloc((size_t)ci.output_width * 3);
    while (ci.output_scanline < ci.output_height) {
        JDIMENSION x;
        jpeg_read_scanlines(&ci, &row, 1);
        for (x = 0; x < ci.output_width; ++x) {
            out->data[off++] = row[3 * x];
            out->data[off++] = row[3 * x + 1];
            out->data[off++] = row[3 * x + 2];
            out->data[off++] = 255;
        }
    }
    free(row);
    jpeg_finish_decompress(&ci);
    jpeg_destroy_decompress(&ci);
    return 0;
}

/* ZipInputStream: first entry only; bytes copied until EOF or the output
 * array is full (the Java loop would throw on overflow and stop). */
static int unzip_first(const uint8_t *in, int32_t n, jobj *out)
{
    uint32_t sig, csize;
    uint16_t method, flags, nlen, xlen;
    z_stream zs;
    int rc;
    if (n < 30) return -1;
    sig = (uint32_t)in[0] | in[1] << 8 | in[2] << 16 | (uint32_t)in[3] << 24;
    if (sig != 0x04034b50u) return -1;
    flags = (uint16_t)(in[6] | in[7] << 8);
    method = (uint16_t)(in[8] | in[9] << 8);
    csize = (uint32_t)in[18] | in[19] << 8 | in[20] << 16 | (uint32_t)in[21] << 24;
    nlen = (uint16_t)(in[26] | in[27] << 8);
    xlen = (uint16_t)(in[28] | in[29] << 8);
    if (30u + nlen + xlen > (uint32_t)n) return -1;
    in += 30 + nlen + xlen;
    n -= 30 + nlen + xlen;
    if (!(flags & 8) && csize <= (uint32_t)n) n = (int32_t)csize;
    if (method == 0) {
        memcpy(out->data, in, (size_t)(n < out->len ? n : out->len));
        return 0;
    }
    if (method != 8) return -1;
    memset(&zs, 0, sizeof zs);
    if (inflateInit2(&zs, -15) != Z_OK) return -1;
    zs.next_in = (Bytef *)in;
    zs.avail_in = (uInt)n;
    zs.next_out = out->data;
    zs.avail_out = (uInt)out->len;
    rc = inflate(&zs, Z_FINISH);
    inflateEnd(&zs);
    return (rc == Z_STREAM_END || rc == Z_BUF_ERROR || rc == Z_OK) ? 0 : -1;
}

/* ---------------------------------------------------------------- files */
static void path_for(char *out, size_t n, const char *dir, const char *name, const char *ext)
{
    snprintf(out, n, "%s/%s%s", dir, name, ext ? ext : "");
}
static int asset_exists(const char *name, const char *ext)
{
    char p[1024];
    struct stat st;
    path_for(p, sizeof p, g_cfg.assets_dir, name, ext);
    return stat(p, &st) == 0;
}

static aot_jvalue op_call_method(void *ctx, void *obj, void *mid, char ret, const aot_jvalue *a, int nargs)
{
    const jmethod *m = (const jmethod *)mid;
    aot_jvalue r;
    char p[1024];
    (void)ctx;
    (void)obj;
    (void)ret;
    (void)nargs;
    r.j = 0;
    if (!strcmp(m->name, "JAVATime")) {
        g_jtime = (int64_t)sm_java_clock_now();
        r.i = (int32_t)g_jtime;
    } else if (!strcmp(m->name, "JAVATimeHi")) {
        r.i = (int32_t)(g_jtime >> 32);
    } else if (!strcmp(m->name, "JAVALoadSample")) {
        const char *name = ((jobj *)a[0].l)->str;
        if (asset_exists(name, ".ogg")) {
            r.i = g_next_sample++;
            g_audio.samples_loaded++;
        } else {
            r.i = -1;
            g_audio.samples_missing++;
            fprintf(stderr, "[java] JAVALoadSample: %s.ogg missing\n", name);
        }
    } else if (!strcmp(m->name, "JAVAPlaySample")) {
        r.i = a[0].i > 0 ? g_next_stream++ : 0;
        g_audio.sample_plays++;
    } else if (!strcmp(m->name, "JAVAStopSample")) {
    } else if (!strcmp(m->name, "JAVASaveFile")) {
        FILE *f;
        jobj *d = (jobj *)a[1].l;
        path_for(p, sizeof p, g_cfg.files_dir, ((jobj *)a[0].l)->str, NULL);
        f = fopen(p, "wb");
        r.i = 0;
        if (f && a[2].i >= 0 && a[2].i <= d->len) {
            fwrite(d->data, 1, (size_t)a[2].i, f);
            r.i = 1;
        }
        if (f) fclose(f);
        if (g_cfg.verbose) fprintf(stderr, "[java] JAVASaveFile %s %d -> %d\n", p, a[2].i, r.i);
    } else if (!strcmp(m->name, "JAVADeleteFile")) {
        path_for(p, sizeof p, g_cfg.files_dir, ((jobj *)a[0].l)->str, NULL);
        remove(p);
    } else if (!strcmp(m->name, "JAVALoadFile")) {
        FILE *f;
        jobj *d = (jobj *)a[1].l;
        path_for(p, sizeof p, g_cfg.files_dir, ((jobj *)a[0].l)->str, NULL);
        f = fopen(p, "rb");
        if (f) {
            size_t want = (size_t)(a[2].i < d->len ? a[2].i : d->len);
            if (fread(d->data, 1, want, f) != want && g_cfg.verbose) fprintf(stderr, "[java] short read %s\n", p);
            fclose(f);
        }
    } else if (!strcmp(m->name, "JAVAFileSize") || !strcmp(m->name, "JAVAFindFile")) {
        struct stat st;
        path_for(p, sizeof p, g_cfg.files_dir, ((jobj *)a[0].l)->str, NULL);
        if (stat(p, &st) == 0) r.i = !strcmp(m->name, "JAVAFindFile") ? 1 : (int32_t)st.st_size;
        else r.i = 0;
    } else if (!strcmp(m->name, "JAVAPlayMusic")) {
        const char *name = ((jobj *)a[0].l)->str;
        g_audio.music_starts++;
        snprintf(g_audio.last_music, sizeof g_audio.last_music, "%s", name);
        if (!asset_exists(name, ".ogg")) fprintf(stderr, "[java] JAVAPlayMusic: %s.ogg missing\n", name);
    } else if (!strcmp(m->name, "JAVASetMusicVolume") || !strcmp(m->name, "JAVAStopMusic") ||
               !strcmp(m->name, "JAVAPauseMusic") || !strcmp(m->name, "JAVAUnPauseMusic") ||
               !strcmp(m->name, "JAVAMusicRestart")) {
    } else if (!strcmp(m->name, "JAVAUnZip") || !strcmp(m->name, "JAVAUnJpg") || !strcmp(m->name, "JAVAUnPng")) {
        jobj *outb = (jobj *)a[0].l, *inb = (jobj *)a[1].l;
        int rc = m->name[6] == 'Z' ? unzip_first(inb->data, inb->len, outb)
                 : m->name[6] == 'J' ? decode_jpg(inb->data, inb->len, outb)
                                     : decode_png(inb->data, inb->len, outb);
        if (rc != 0) fprintf(stderr, "[java] %s failed (%d bytes in, %d out)\n", m->name, inb->len, outb->len);
    } else if (!strcmp(m->name, "JAVAOpenFeintLastLoggedInUserID")) {
        jobj *d = (jobj *)a[0].l;
        if (d->len > 0) d->data[0] = 0; /* OpenFeint removed: no current user */
    } else if (!strcmp(m->name, "JAVAOpenFeintIsUserLoggedIn") || !strcmp(m->name, "JAVAOpenFeintIsOnline")) {
        r.i = 0;
    } else if (!strncmp(m->name, "JAVAOpenFeint", 13)) {
        /* Open/Submit/Unlock: service defunct and removed; no callback */
    } else if (!strcmp(m->name, "JAVAVibrate")) {
        g_audio.vibrations++;
    } else {
        fprintf(stderr, "[java] unhandled method %s\n", m->name);
        abort();
    }
    return r;
}

static aot_java_ops g_ops = {
    op_find_class, op_get_object_class, op_new_global_ref, op_delete_ref, op_is_same_object,
    op_get_method_id, op_get_field_id, op_get_int_field, op_call_method, op_new_string_utf,
    op_get_string_utf_chars, op_new_byte_array, op_get_region, op_set_region,
};

const aot_java_ops *sm_java_emul_ops(const sm_java_emul_cfg *cfg)
{
    g_cfg = *cfg;
    return &g_ops;
}
