#import <AVFoundation/AVFoundation.h>
#import <AudioToolbox/AudioToolbox.h>
#import <CoreGraphics/CoreGraphics.h>
#import <Foundation/Foundation.h>
#import <ImageIO/ImageIO.h>
#import <mach/mach_time.h>

#include <math.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <zlib.h>

#include "SMIOSJava.h"

typedef NS_ENUM(NSInteger, SMJKind) {
    SMJClass = 1, SMJRenderer, SMJFD, SMJString, SMJBytes,
};

@interface SMJObject : NSObject
@property(nonatomic) SMJKind kind;
@property(nonatomic, copy) NSString *name;
@property(nonatomic, strong) NSMutableData *data;
@property(nonatomic) int fd;
@property(nonatomic, weak) SMJObject *cls;
@end
@implementation SMJObject
@end

typedef struct { const char *name; const char *sig; } sm_jmethod;
static const sm_jmethod k_methods[] = {
    {"JAVALoadSample", "(Ljava/lang/String;)I"}, {"JAVAPlaySample", "(IF)I"},
    {"JAVAStopSample", "(I)V"}, {"JAVASaveFile", "(Ljava/lang/String;[BI)I"},
    {"JAVADeleteFile", "(Ljava/lang/String;)V"}, {"JAVALoadFile", "(Ljava/lang/String;[BI)V"},
    {"JAVAFindFile", "(Ljava/lang/String;)I"}, {"JAVAFileSize", "(Ljava/lang/String;)I"},
    {"JAVASetMusicVolume", "(F)V"}, {"JAVAPlayMusic", "(Ljava/lang/String;)V"},
    {"JAVAStopMusic", "()V"}, {"JAVAUnPauseMusic", "()V"}, {"JAVAPauseMusic", "()V"},
    {"JAVAMusicRestart", "()V"}, {"JAVAUnZip", "([B[B)V"}, {"JAVAUnJpg", "([B[B)V"},
    {"JAVAUnPng", "([B[B)V"}, {"JAVAOpenFeintOpen", "()V"},
    {"JAVAOpenFeintLastLoggedInUserID", "([B)V"},
    {"JAVAOpenFeintSubmit", "(Ljava/lang/String;II)V"},
    {"JAVAOpenFeintUnlock", "(Ljava/lang/String;I)V"},
    {"JAVAOpenFeintIsUserLoggedIn", "()I"}, {"JAVAOpenFeintIsOnline", "()I"},
    {"JAVATime", "()I"}, {"JAVATimeHi", "()I"}, {"JAVAVibrate", "(I)V"},
};
static const char k_fd_field[] = "descriptor";

static NSString *g_assets;
static NSString *g_files;
static BOOL g_verbose;
static NSMutableDictionary<NSString *, SMJObject *> *g_classes;
static NSMutableSet<SMJObject *> *g_dynamic;
static SMJObject *g_renderer;
static int64_t g_jtime;
static int g_next_sample = 1, g_next_stream = 1;
static NSMutableDictionary<NSNumber *, NSURL *> *g_samples;
static NSMutableDictionary<NSNumber *, AVAudioPlayer *> *g_streams;
static AVAudioPlayer *g_music;
static float g_music_volume = 1.0f;

static SMJObject *JObj(void *p) { return (__bridge SMJObject *)p; }
static void *JP(SMJObject *o) { return (__bridge void *)o; }

static uint64_t monotonic_ns(void) {
    static mach_timebase_info_data_t tb;
    static dispatch_once_t once;
    dispatch_once(&once, ^{ mach_timebase_info(&tb); });
    uint64_t t = mach_absolute_time();
    __uint128_t n = (__uint128_t)t * tb.numer;
    return (uint64_t)(n / tb.denom);
}

static SMJObject *new_obj(SMJKind kind) {
    SMJObject *o = [SMJObject new];
    o.kind = kind;
    return o;
}

void *sm_ios_java_class(const char *name) {
    if (!name) return NULL;
    if (!g_classes) g_classes = [NSMutableDictionary dictionary];
    NSString *key = [NSString stringWithUTF8String:name];
    SMJObject *o = g_classes[key];
    if (o) return JP(o);
    BOOL known = [key isEqualToString:@"java/io/FileDescriptor"] ||
                 [key isEqualToString:@"java/lang/String"] ||
                 [key isEqualToString:@"com/sandlotgames/snailmail/ADRenderer"] ||
                 [key hasPrefix:@"com/sandlotgames/snailmail/"];
    if (!known) {
        NSLog(@"[SnailMail/iOS] FindClass(%@): unknown pseudo-Java class", key);
        return NULL;
    }
    o = new_obj(SMJClass);
    o.name = key;
    g_classes[key] = o;
    return JP(o);
}

void *sm_ios_java_renderer(void) {
    if (!g_renderer) {
        g_renderer = new_obj(SMJRenderer);
        g_renderer.name = @"ADRenderer";
        g_renderer.cls = JObj(sm_ios_java_class("com/sandlotgames/snailmail/ADRenderer"));
    }
    return JP(g_renderer);
}

void *sm_ios_java_file_descriptor(int fd) {
    SMJObject *o = new_obj(SMJFD);
    o.fd = fd;
    o.cls = JObj(sm_ios_java_class("java/io/FileDescriptor"));
    [g_dynamic addObject:o];
    return JP(o);
}

static void *op_find_class(void *ctx, const char *name) { (void)ctx; return sm_ios_java_class(name); }
static void *op_get_object_class(void *ctx, void *obj) { (void)ctx; return obj ? JP(JObj(obj).cls) : NULL; }
static void *op_new_global_ref(void *ctx, void *obj) { (void)ctx; return obj; }
static void op_delete_ref(void *ctx, void *obj) {
    (void)ctx;
    SMJObject *o = JObj(obj);
    if (o && (o.kind == SMJString || o.kind == SMJBytes)) [g_dynamic removeObject:o];
}
static int op_is_same_object(void *ctx, void *a, void *b) { (void)ctx; return a == b; }

static void *op_get_method_id(void *ctx, void *cls, const char *name, const char *sig) {
    (void)ctx; (void)cls;
    for (size_t i = 0; i < sizeof k_methods / sizeof k_methods[0]; ++i)
        if (!strcmp(k_methods[i].name, name) && !strcmp(k_methods[i].sig, sig)) return (void *)&k_methods[i];
    NSLog(@"[SnailMail/iOS] GetMethodID(%s %s): unknown", name, sig);
    return NULL;
}

static void *op_get_field_id(void *ctx, void *cls, const char *name, const char *sig) {
    (void)ctx;
    SMJObject *c = JObj(cls);
    if (c && [c.name isEqualToString:@"java/io/FileDescriptor"] &&
        !strcmp(name, "descriptor") && !strcmp(sig, "I")) return (void *)k_fd_field;
    return NULL;
}

static int32_t op_get_int_field(void *ctx, void *obj, void *fid) {
    (void)ctx;
    SMJObject *o = JObj(obj);
    return (fid == (void *)k_fd_field && o.kind == SMJFD) ? o.fd : 0;
}

static void *op_new_string_utf(void *ctx, const char *s) {
    (void)ctx;
    SMJObject *o = new_obj(SMJString);
    o.name = s ? [NSString stringWithUTF8String:s] : @"";
    o.cls = JObj(sm_ios_java_class("java/lang/String"));
    [g_dynamic addObject:o];
    return JP(o);
}

static char *op_get_string_utf_chars(void *ctx, void *str) {
    (void)ctx;
    NSString *s = JObj(str).name ?: @"";
    return strdup(s.UTF8String ?: "");
}

static void *op_new_byte_array(void *ctx, int32_t len) {
    (void)ctx;
    if (len < 0) return NULL;
    SMJObject *o = new_obj(SMJBytes);
    o.data = [NSMutableData dataWithLength:(NSUInteger)len];
    [g_dynamic addObject:o];
    return JP(o);
}

static void op_get_region(void *ctx, void *arr, int32_t start, int32_t len, void *buf) {
    (void)ctx;
    SMJObject *o = JObj(arr);
    if (!o || o.kind != SMJBytes || start < 0 || len < 0 ||
        (NSUInteger)(start + len) > o.data.length) abort();
    memcpy(buf, (const uint8_t *)o.data.bytes + start, (size_t)len);
}

static void op_set_region(void *ctx, void *arr, int32_t start, int32_t len, const void *buf) {
    (void)ctx;
    SMJObject *o = JObj(arr);
    if (!o || o.kind != SMJBytes || start < 0 || len < 0 ||
        (NSUInteger)(start + len) > o.data.length) abort();
    memcpy((uint8_t *)o.data.mutableBytes + start, buf, (size_t)len);
}

static NSString *file_path(NSString *name) {
    return [g_files stringByAppendingPathComponent:name ?: @""];
}

static NSURL *audio_url(NSString *base) {
    if (!base.length) return nil;
    NSString *m4a = [g_assets stringByAppendingPathComponent:[base stringByAppendingString:@".m4a"]];
    if ([[NSFileManager defaultManager] fileExistsAtPath:m4a]) return [NSURL fileURLWithPath:m4a];
    NSString *ogg = [g_assets stringByAppendingPathComponent:[base stringByAppendingString:@".ogg"]];
    if ([[NSFileManager defaultManager] fileExistsAtPath:ogg]) return [NSURL fileURLWithPath:ogg];
    return nil;
}

static int unzip_first(NSData *in, NSMutableData *out) {
    if (in.length < 30) return -1;
    const uint8_t *p = in.bytes;
    uint32_t sig = (uint32_t)p[0] | (uint32_t)p[1] << 8 | (uint32_t)p[2] << 16 | (uint32_t)p[3] << 24;
    if (sig != 0x04034b50u) return -1;
    uint16_t flags = (uint16_t)(p[6] | p[7] << 8);
    uint16_t method = (uint16_t)(p[8] | p[9] << 8);
    uint32_t csize = (uint32_t)p[18] | (uint32_t)p[19] << 8 | (uint32_t)p[20] << 16 | (uint32_t)p[21] << 24;
    uint16_t nlen = (uint16_t)(p[26] | p[27] << 8), xlen = (uint16_t)(p[28] | p[29] << 8);
    size_t off = 30u + nlen + xlen;
    if (off > in.length) return -1;
    size_t n = in.length - off;
    if (!(flags & 8) && csize <= n) n = csize;
    p += off;
    if (method == 0) {
        memcpy(out.mutableBytes, p, MIN(n, out.length));
        return 0;
    }
    if (method != 8) return -1;
    z_stream zs;
    memset(&zs, 0, sizeof zs);
    if (inflateInit2(&zs, -15) != Z_OK) return -1;
    zs.next_in = (Bytef *)p;
    zs.avail_in = (uInt)n;
    zs.next_out = out.mutableBytes;
    zs.avail_out = (uInt)out.length;
    int rc = inflate(&zs, Z_FINISH);
    inflateEnd(&zs);
    return (rc == Z_STREAM_END || rc == Z_BUF_ERROR || rc == Z_OK) ? 0 : -1;
}

static int decode_image(NSData *in, NSMutableData *out) {
    CGImageSourceRef src = CGImageSourceCreateWithData((__bridge CFDataRef)in, NULL);
    if (!src) return -1;
    CGImageRef image = CGImageSourceCreateImageAtIndex(src, 0, NULL);
    CFRelease(src);
    if (!image) return -1;
    size_t w = CGImageGetWidth(image), h = CGImageGetHeight(image);
    if (!w || !h || w > SIZE_MAX / h / 4 || w * h * 4 > out.length) {
        CGImageRelease(image);
        return -1;
    }
    CGColorSpaceRef cs = CGColorSpaceCreateDeviceRGB();
    CGContextRef cg = CGBitmapContextCreate(out.mutableBytes, w, h, 8, w * 4, cs,
                                            kCGImageAlphaPremultipliedLast | kCGBitmapByteOrder32Big);
    CGColorSpaceRelease(cs);
    if (!cg) { CGImageRelease(image); return -1; }
    CGContextTranslateCTM(cg, 0, (CGFloat)h);
    CGContextScaleCTM(cg, 1, -1);
    CGContextSetBlendMode(cg, kCGBlendModeCopy);
    CGContextDrawImage(cg, CGRectMake(0, 0, w, h), image);
    CGContextRelease(cg);
    CGImageRelease(image);
    return 0;
}

static void reap_finished_streams(void) {
    for (NSNumber *key in g_streams.allKeys)
        if (!g_streams[key].isPlaying) [g_streams removeObjectForKey:key];
}

static aot_jvalue op_call_method(void *ctx, void *obj, void *mid, char ret,
                                 const aot_jvalue *a, int nargs) {
    (void)ctx; (void)obj; (void)ret; (void)nargs;
    const sm_jmethod *m = (const sm_jmethod *)mid;
    aot_jvalue r; r.j = 0;
    NSString *name = m ? [NSString stringWithUTF8String:m->name] : @"";

    if ([name isEqualToString:@"JAVATime"]) {
        g_jtime = (int64_t)monotonic_ns(); r.i = (int32_t)g_jtime;
    } else if ([name isEqualToString:@"JAVATimeHi"]) {
        r.i = (int32_t)(g_jtime >> 32);
    } else if ([name isEqualToString:@"JAVALoadSample"]) {
        NSURL *url = audio_url(JObj(a[0].l).name);
        if (url) { int sid = g_next_sample++; g_samples[@(sid)] = url; r.i = sid; }
        else r.i = -1;
    } else if ([name isEqualToString:@"JAVAPlaySample"]) {
        reap_finished_streams();
        NSURL *url = g_samples[@(a[0].i)];
        if (url) {
            NSError *err = nil;
            AVAudioPlayer *p = [[AVAudioPlayer alloc] initWithContentsOfURL:url error:&err];
            if (p) {
                p.volume = fmaxf(0.f, fminf(1.f, a[1].f));
                [p prepareToPlay]; [p play];
                int stream = g_next_stream++; g_streams[@(stream)] = p; r.i = stream;
            } else if (g_verbose) NSLog(@"[SnailMail/iOS] sample decode failed: %@", err);
        }
    } else if ([name isEqualToString:@"JAVAStopSample"]) {
        AVAudioPlayer *p = g_streams[@(a[0].i)]; [p stop]; [g_streams removeObjectForKey:@(a[0].i)];
    } else if ([name isEqualToString:@"JAVASaveFile"]) {
        SMJObject *bytes = JObj(a[1].l); int32_t n = a[2].i;
        if (n >= 0 && (NSUInteger)n <= bytes.data.length) {
            NSData *d = [bytes.data subdataWithRange:NSMakeRange(0, (NSUInteger)n)];
            r.i = [d writeToFile:file_path(JObj(a[0].l).name) options:NSDataWritingAtomic error:nil] ? 1 : 0;
        }
    } else if ([name isEqualToString:@"JAVADeleteFile"]) {
        [[NSFileManager defaultManager] removeItemAtPath:file_path(JObj(a[0].l).name) error:nil];
    } else if ([name isEqualToString:@"JAVALoadFile"]) {
        NSData *d = [NSData dataWithContentsOfFile:file_path(JObj(a[0].l).name)];
        SMJObject *bytes = JObj(a[1].l);
        size_t requested = a[2].i > 0 ? (size_t)a[2].i : 0;
        size_t n = MIN(requested, bytes.data.length);
        n = MIN(n, d.length);
        if (n) memcpy(bytes.data.mutableBytes, d.bytes, n);
    } else if ([name isEqualToString:@"JAVAFindFile"] || [name isEqualToString:@"JAVAFileSize"]) {
        NSDictionary *at = [[NSFileManager defaultManager] attributesOfItemAtPath:file_path(JObj(a[0].l).name) error:nil];
        NSNumber *size = at[NSFileSize];
        r.i = at ? ([name isEqualToString:@"JAVAFindFile"] ? 1 : (int32_t)size.longLongValue) : 0;
    } else if ([name isEqualToString:@"JAVAPlayMusic"]) {
        NSURL *url = audio_url(JObj(a[0].l).name);
        NSError *err = nil;
        g_music = url ? [[AVAudioPlayer alloc] initWithContentsOfURL:url error:&err] : nil;
        if (g_music) {
            g_music.numberOfLoops = -1; g_music.volume = g_music_volume;
            [g_music prepareToPlay]; [g_music play];
        } else if (g_verbose) NSLog(@"[SnailMail/iOS] music unavailable: %@", err);
    } else if ([name isEqualToString:@"JAVASetMusicVolume"]) {
        g_music_volume = fmaxf(0.f, fminf(1.f, a[0].f)); g_music.volume = g_music_volume;
    } else if ([name isEqualToString:@"JAVAStopMusic"]) {
        [g_music stop]; g_music = nil;
    } else if ([name isEqualToString:@"JAVAPauseMusic"]) {
        [g_music pause];
    } else if ([name isEqualToString:@"JAVAUnPauseMusic"]) {
        [g_music play];
    } else if ([name isEqualToString:@"JAVAMusicRestart"]) {
        g_music.currentTime = 0; [g_music play];
    } else if ([name isEqualToString:@"JAVAUnZip"]) {
        if (unzip_first(JObj(a[1].l).data, JObj(a[0].l).data) != 0)
            NSLog(@"[SnailMail/iOS] JAVAUnZip failed");
    } else if ([name isEqualToString:@"JAVAUnJpg"] || [name isEqualToString:@"JAVAUnPng"]) {
        if (decode_image(JObj(a[1].l).data, JObj(a[0].l).data) != 0)
            NSLog(@"[SnailMail/iOS] %@ failed", name);
    } else if ([name isEqualToString:@"JAVAOpenFeintLastLoggedInUserID"]) {
        SMJObject *bytes = JObj(a[0].l);
        if (bytes.data.length) ((uint8_t *)bytes.data.mutableBytes)[0] = 0;
    } else if ([name isEqualToString:@"JAVAOpenFeintIsUserLoggedIn"] ||
               [name isEqualToString:@"JAVAOpenFeintIsOnline"]) {
        r.i = 0;
    } else if ([name hasPrefix:@"JAVAOpenFeint"]) {
        /* Defunct service: intentionally offline. */
    } else if ([name isEqualToString:@"JAVAVibrate"]) {
        AudioServicesPlaySystemSound(kSystemSoundID_Vibrate);
    } else {
        NSLog(@"[SnailMail/iOS] unhandled pseudo-Java callback %@", name);
        abort();
    }
    return r;
}

static aot_java_ops g_ops = {
    op_find_class, op_get_object_class, op_new_global_ref, op_delete_ref, op_is_same_object,
    op_get_method_id, op_get_field_id, op_get_int_field, op_call_method, op_new_string_utf,
    op_get_string_utf_chars, op_new_byte_array, op_get_region, op_set_region,
};

const aot_java_ops *sm_ios_java_ops(const sm_ios_java_config *cfg) {
    @autoreleasepool {
        g_assets = cfg && cfg->assets_dir ? [NSString stringWithUTF8String:cfg->assets_dir] : @"";
        g_files = cfg && cfg->files_dir ? [NSString stringWithUTF8String:cfg->files_dir] : @"";
        g_verbose = cfg && cfg->verbose;
        g_classes = [NSMutableDictionary dictionary];
        g_dynamic = [NSMutableSet set];
        g_samples = [NSMutableDictionary dictionary];
        g_streams = [NSMutableDictionary dictionary];
        [[NSFileManager defaultManager] createDirectoryAtPath:g_files
                                  withIntermediateDirectories:YES attributes:nil error:nil];
        AVAudioSession *session = AVAudioSession.sharedInstance;
        [session setCategory:AVAudioSessionCategoryAmbient mode:AVAudioSessionModeDefault
                     options:AVAudioSessionCategoryOptionMixWithOthers error:nil];
        [session setActive:YES error:nil];
    }
    return &g_ops;
}
