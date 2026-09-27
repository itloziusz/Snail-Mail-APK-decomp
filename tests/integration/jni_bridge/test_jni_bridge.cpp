// Integration test of android/app/src/main/cpp/jni_bridge.cpp outside Android.
//
// The bridge is compiled unchanged against a scripted fake JNIEnv (only the
// JNI functions the bridge uses are provided; any other slot is NULL and would
// crash loudly) and a capture stub of liblog. It checks:
//   1. JNIDatInit, given the ORIGINAL APK's fd with asm.mp3's zip data offset
//      and length (what AssetManager.openFd yields on a device), duplicates the
//      fd through ParcelFileDescriptor.dup(...).detachFd() and indexes the real
//      archive: expects the INFO log "734 records ... 0 FAILED".
//   2. JNIDatInit with a null FileDescriptor logs an error and does not throw.
//   3. Unreconstructed entry points (nativeInit, nativeRender, JNIKey) throw
//      java.lang.IllegalStateException: fail loudly, never fake success.
//   4. Trivial reconstructed bodies: JNIDebug returns 0.
// Exit 77 (SKIP) when the gitignored original APK is absent.
#include <jni.h>

#include <cstdarg>
#include <cstdint>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <fcntl.h>
#include <string>
#include <unistd.h>
#include <vector>

extern "C" {
JNIEXPORT void JNICALL Java_com_sandlotgames_snailmail_SnailMailActivity_JNIDatInit(JNIEnv*, jclass, jobject,
                                                                                  jint, jint);
JNIEXPORT jint JNICALL Java_com_sandlotgames_snailmail_SnailMailActivity_JNIDebug(JNIEnv*, jclass);
JNIEXPORT void JNICALL Java_com_sandlotgames_snailmail_ADRenderer_nativeInit(JNIEnv*, jobject);
JNIEXPORT void JNICALL Java_com_sandlotgames_snailmail_ADRenderer_nativeRender(JNIEnv*, jobject, jint);
JNIEXPORT void JNICALL Java_com_sandlotgames_snailmail_ADGLSurfaceView_JNIKey(JNIEnv*, jobject, jint);

// liblog capture stub (the bridge links against these names on Android).
std::vector<std::string> g_log;
int __android_log_write(int prio, const char* tag, const char* text) {
    g_log.push_back(std::to_string(prio) + " " + tag + " " + text);
    return 1;
}
int __android_log_print(int prio, const char* tag, const char* fmt, ...) {
    char buf[1024];
    va_list ap;
    va_start(ap, fmt);
    std::vsnprintf(buf, sizeof buf, fmt, ap);
    va_end(ap);
    return __android_log_write(prio, tag, buf);
}
}

namespace {

int g_failures = 0;
#define EXPECT(c)                                                             \
    do {                                                                      \
        if (!(c)) {                                                           \
            ++g_failures;                                                     \
            std::fprintf(stderr, "%s:%d: EXPECT failed: %s\n", __FILE__, __LINE__, #c); \
        }                                                                     \
    } while (0)

// Fake object handles.
_jobject g_pfd_class_obj, g_pfd_obj, g_ise_class_obj, g_fd_obj;
jclass const kPfdClass = reinterpret_cast<jclass>(&g_pfd_class_obj);
jclass const kIseClass = reinterpret_cast<jclass>(&g_ise_class_obj);
jobject const kPfd = &g_pfd_obj;
jobject const kFdObj = &g_fd_obj;
jmethodID const kDup = reinterpret_cast<jmethodID>(0x1001);
jmethodID const kDetach = reinterpret_cast<jmethodID>(0x1002);

int g_java_fd = -1;          // the "FileDescriptor" wrapped by kFdObj
bool g_pending = false;      // pending Java exception
std::string g_thrown_class, g_thrown_msg;
int g_detached = -1;

jclass JNICALL FindClass(JNIEnv*, const char* name) {
    if (std::strcmp(name, "android/os/ParcelFileDescriptor") == 0) return kPfdClass;
    if (std::strcmp(name, "java/lang/IllegalStateException") == 0) return kIseClass;
    return nullptr;
}
jmethodID JNICALL GetStaticMethodID(JNIEnv*, jclass c, const char* n, const char* sig) {
    if (c == kPfdClass && !std::strcmp(n, "dup") &&
        !std::strcmp(sig, "(Ljava/io/FileDescriptor;)Landroid/os/ParcelFileDescriptor;"))
        return kDup;
    return nullptr;
}
jmethodID JNICALL GetMethodID(JNIEnv*, jclass c, const char* n, const char* sig) {
    if (c == kPfdClass && !std::strcmp(n, "detachFd") && !std::strcmp(sig, "()I")) return kDetach;
    return nullptr;
}
jobject JNICALL CallStaticObjectMethodV(JNIEnv*, jclass c, jmethodID m, va_list ap) {
    jobject arg = va_arg(ap, jobject);
    if (c == kPfdClass && m == kDup && arg == kFdObj) {
        g_detached = dup(g_java_fd);  // ParcelFileDescriptor.dup() semantics
        return kPfd;
    }
    g_pending = true;
    return nullptr;
}
jint JNICALL CallIntMethodV(JNIEnv*, jobject o, jmethodID m, va_list) {
    if (o == kPfd && m == kDetach) {
        int fd = g_detached;
        g_detached = -1;
        return fd;
    }
    g_pending = true;
    return -1;
}
jboolean JNICALL ExceptionCheck(JNIEnv*) { return g_pending ? JNI_TRUE : JNI_FALSE; }
void JNICALL ExceptionClear(JNIEnv*) { g_pending = false; }
void JNICALL DeleteLocalRef(JNIEnv*, jobject) {}
jint JNICALL ThrowNew(JNIEnv*, jclass c, const char* msg) {
    g_thrown_class = (c == kIseClass) ? "java/lang/IllegalStateException" : "?";
    g_thrown_msg = msg;
    g_pending = true;
    return 0;
}

bool log_contains(const char* needle) {
    for (const auto& l : g_log)
        if (l.find(needle) != std::string::npos) return true;
    return false;
}

uint32_t le32(const uint8_t* p) { return p[0] | (p[1] << 8) | (p[2] << 16) | (uint32_t(p[3]) << 24); }
uint16_t le16(const uint8_t* p) { return uint16_t(p[0] | (p[1] << 8)); }

// Data offset/size of a STORED entry, from the central directory.
bool zip_find(const std::vector<uint8_t>& z, const char* name, long* off, long* size) {
    size_t len = z.size(), i;
    if (len < 22) return false;
    for (i = len - 22; le32(&z[i]) != 0x06054b50u; --i)
        if (i == 0) return false;
    uint32_t n = le16(&z[i + 10]), p = le32(&z[i + 16]);
    while (n-- && p + 46 <= len && le32(&z[p]) == 0x02014b50u) {
        uint16_t fl = le16(&z[p + 28]), xl = le16(&z[p + 30]), cl = le16(&z[p + 32]);
        if (fl == std::strlen(name) && !std::memcmp(&z[p + 46], name, fl)) {
            uint32_t lho = le32(&z[p + 42]);
            *off = long(lho) + 30 + le16(&z[lho + 26]) + le16(&z[lho + 28]);
            *size = long(le32(&z[p + 20]));
            return le16(&z[p + 10]) == 0;
        }
        p += 46u + fl + xl + cl;
    }
    return false;
}

}  // namespace

int main() {
    const char* apk = SM_REPO_ROOT "/original/com.sandlotgames.snailmail-1.00.apk";
    FILE* f = std::fopen(apk, "rb");
    if (!f) {
        std::printf("SKIP: %s absent (see original/README.md)\n", apk);
        return 77;
    }
    std::vector<uint8_t> bytes;
    for (int c; (c = std::fgetc(f)) != EOF;) bytes.push_back(uint8_t(c));
    std::fclose(f);
    long off = 0, size = 0;
    EXPECT(zip_find(bytes, "assets/asm.mp3", &off, &size));

    JNINativeInterface_ fns;
    std::memset(&fns, 0, sizeof fns);  // unused slots stay NULL: any other call crashes loudly
    fns.FindClass = FindClass;
    fns.GetStaticMethodID = GetStaticMethodID;
    fns.GetMethodID = GetMethodID;
    fns.CallStaticObjectMethodV = CallStaticObjectMethodV;
    fns.CallIntMethodV = CallIntMethodV;
    fns.ExceptionCheck = ExceptionCheck;
    fns.ExceptionClear = ExceptionClear;
    fns.DeleteLocalRef = DeleteLocalRef;
    fns.ThrowNew = ThrowNew;
    JNIEnv env;
    env.functions = &fns;

    // 1. The on-device JNIDatInit call: (APK fd, start offset, length).
    g_java_fd = open(apk, O_RDONLY);
    EXPECT(g_java_fd >= 0);
    Java_com_sandlotgames_snailmail_SnailMailActivity_JNIDatInit(&env, nullptr, kFdObj, jint(off), jint(size));
    EXPECT(!g_pending);
    EXPECT(log_contains("734 records, directory 34701 bytes"));
    EXPECT(log_contains("733 self, 1 shadowed duplicate(s), 0 FAILED"));
    close(g_java_fd);  // the bridge must hold its own duplicate

    // 2. Null FileDescriptor: error log, no exception (the original just returned).
    size_t before = g_log.size();
    Java_com_sandlotgames_snailmail_SnailMailActivity_JNIDatInit(&env, nullptr, nullptr, 0, 0);
    EXPECT(!g_pending);
    EXPECT(g_log.size() == before + 1 && log_contains("null FileDescriptor"));

    // 3. Unreconstructed entry points fail loudly.
    struct { const char* sym; void (*call)(JNIEnv*); } loud[] = {
        {"nativeInit", [](JNIEnv* e) { Java_com_sandlotgames_snailmail_ADRenderer_nativeInit(e, nullptr); }},
        {"nativeRender", [](JNIEnv* e) { Java_com_sandlotgames_snailmail_ADRenderer_nativeRender(e, nullptr, 0); }},
        {"JNIKey", [](JNIEnv* e) { Java_com_sandlotgames_snailmail_ADGLSurfaceView_JNIKey(e, nullptr, 62); }},
    };
    for (auto& l : loud) {
        g_pending = false;
        g_thrown_class.clear();
        l.call(&env);
        EXPECT(g_pending);
        EXPECT(g_thrown_class == "java/lang/IllegalStateException");
        EXPECT(g_thrown_msg.find(l.sym) != std::string::npos);
        EXPECT(g_thrown_msg.find("not reconstructed") != std::string::npos);
    }
    g_pending = false;

    // 4. Trivial established body.
    EXPECT(Java_com_sandlotgames_snailmail_SnailMailActivity_JNIDebug(&env, nullptr) == 0);

    for (const auto& l : g_log) std::printf("log: %s\n", l.c_str());
    if (g_failures) {
        std::fprintf(stderr, "jni_bridge integration: %d failure(s)\n", g_failures);
        return 1;
    }
    std::printf("jni_bridge integration: all checks passed\n");
    return 0;
}
