// JNI bridge of the Snail Mail arm64-v8a port (platform glue, not game code).
//
// Exports every JNI entry point that the original libsnailmail.so exported
// (18 Java_com_sandlotgames_snailmail_* symbols, identical in the v7a build
// e43bc913... and the v5 build 96dbeaeb...). Names and descriptors must match
// the Java shell in app/src/main/java/com/sandlotgames/snailmail/.
//
// Policy (docs/CONVENTIONS.md, docs/PLATFORM_BOUNDARIES.md):
//  * An entry point whose original body is not yet reconstructed FAILS LOUDLY:
//    it logs at ERROR level and throws java.lang.IllegalStateException naming
//    the symbol and the original address. There is no fake success anywhere.
//  * Only entry points whose complete original behaviour is established from
//    the bytes and is trivial are implemented here (listed below with evidence).
//    Everything else must be routed to reconstructed/<area> code once it exists.
//
// Entry points implemented from established evidence (v7a addresses):
//   ADRenderer.JNIAudioInit           0x13ad0  "bx lr"                -> no-op
//   SnailMailActivity.JNIDebug        0x13b44  "mov r0,#0; bx lr"     -> return 0
//   SnailMailActivity.JNIDatUnInit    0x13c70  wprintf(); wprintf();  -> no-op
//                                               (wprintf 0x19948 is empty)
//   ADRenderer.nativeDone             0x1555c  appDeinit(); importGLDeinit();
//                                               (0x1568c, 0x15498 are "bx lr") -> no-op
// Entry points reconstructed in reconstructed/<area> and wired here:
//   SnailMailActivity.JNIDatInit      0x15244  -> sm_platform/dat (sm_assets directory
//                                               + cRHash index; unit- and
//                                               reference-tested, see docs/PORTING_STATUS.md)
// Entry points that fail loudly (unreconstructed):
//   ADGLSurfaceView.JNIKey 0x13d78, AccelerometerListener.JNIAccelerometer 0x142bc,
//   SnailMailActivity.JNIResourceManagerInvalidate 0x142fc,
//   ADGLSurfaceView.JNIMouseEvent 0x14318,
//   ADRenderer.nativeRender 0x1549c, ADGLSurfaceView.nativePause 0x15500,
//   ADRenderer.nativeResize 0x1556c, ADRenderer.nativeReInit 0x155f0,
//   ADRenderer.nativeInit 0x15650, ADRenderer.JNIOFOUnlockCB 0x7d404,
//   ADRenderer.JNIOFOSubmitCB 0x7d44c, SnailMailActivity.JNIOFOSave 0x7d71c,
//   MyOpenFeintDelegate.JNIOFOInit 0x7d81c (class removed from the shell).
//
// Porting notes for whoever replaces a stub (docs/ABI_PORTING.md):
//  * the original cached the JNIEnv* and the ADRenderer jobject *local*
//    reference in globals (JAVA_RegisterFunctions 0x13ca4); a port must use
//    NewGlobalRef and must only use a JNIEnv on its own thread;
//  * JNIOFOSubmitCB/JNIOFOUnlockCB receive a Java int that was a native
//    pointer in the original (written through at 0x7d42c / 0x7d474); on
//    arm64 that needs a handle table, never a pointer cast.

#include <jni.h>
#include <android/log.h>
#include <unistd.h>

#include <cstdio>

#include "sm_platform/dat.h"

namespace {

constexpr const char* kTag = "SnailMailPort";

void fail_unreconstructed(JNIEnv* env, const char* symbol, const char* v7a_addr) {
    char msg[320];
    std::snprintf(msg, sizeof msg,
                  "native entry point %s (original v7a:%s) is not reconstructed yet; "
                  "refusing to continue (see docs/PLATFORM_BOUNDARIES.md)",
                  symbol, v7a_addr);
    __android_log_write(ANDROID_LOG_ERROR, kTag, msg);
    if (env->ExceptionCheck()) {
        return;  // keep the first pending exception
    }
    jclass cls = env->FindClass("java/lang/IllegalStateException");
    if (cls != nullptr) {
        env->ThrowNew(cls, msg);
        env->DeleteLocalRef(cls);
    }
}

// The archive handle: gJavaAssetFid/Start/Length + gDat + gDatHash in the
// original (JNIDatInit v7a:0x15244). Written on the main thread in onCreate,
// read later on the GL thread; ordering is provided by the framework as in the
// original (docs/BOOT_CHAIN.md "Managed side").
sm_dat g_dat = {-1, 0, 0, {}, SM_ASM_OK, 0};

// Original: GetFieldID(FileDescriptor, "descriptor", "I") + GetIntField, then
// dup() (v7a:0x15260-0x152f0). "descriptor" is a private, non-SDK field, so the
// port obtains the same owned duplicate through public API instead:
// ParcelFileDescriptor.dup(FileDescriptor).detachFd(). Returns -1 on failure
// with no Java exception left pending.
int dup_java_fd(JNIEnv* env, jobject fd_obj) {
    jclass pfd_cls = env->FindClass("android/os/ParcelFileDescriptor");
    if (pfd_cls == nullptr) {
        env->ExceptionClear();
        return -1;
    }
    int fd = -1;
    jmethodID dup = env->GetStaticMethodID(pfd_cls, "dup",
                                           "(Ljava/io/FileDescriptor;)Landroid/os/ParcelFileDescriptor;");
    jmethodID detach = env->GetMethodID(pfd_cls, "detachFd", "()I");
    if (dup != nullptr && detach != nullptr) {
        jobject pfd = env->CallStaticObjectMethod(pfd_cls, dup, fd_obj);
        if (!env->ExceptionCheck() && pfd != nullptr) {
            fd = env->CallIntMethod(pfd, detach);
            if (env->ExceptionCheck()) {
                fd = -1;
            }
        }
        if (pfd != nullptr) {
            env->DeleteLocalRef(pfd);
        }
    }
    if (env->ExceptionCheck()) {
        env->ExceptionClear();
    }
    env->DeleteLocalRef(pfd_cls);
    return fd;
}

}  // namespace

#define SM_FAIL(sym, addr) fail_unreconstructed(env, #sym, addr)

extern "C" {

// ---------------------------------------------------------------- established trivial bodies

JNIEXPORT void JNICALL
Java_com_sandlotgames_snailmail_ADRenderer_JNIAudioInit(JNIEnv*, jobject) {
    // v7a:0x13ad0 (4 bytes): bx lr
}

JNIEXPORT jint JNICALL
Java_com_sandlotgames_snailmail_SnailMailActivity_JNIDebug(JNIEnv*, jclass) {
    // v7a:0x13b44: mov r0, #0 ; bx lr
    return 0;
}

JNIEXPORT void JNICALL
Java_com_sandlotgames_snailmail_SnailMailActivity_JNIDatUnInit(JNIEnv*, jclass) {
    // v7a:0x13c70-0x13c94: wprintf(str) ; tail-call wprintf(str). wprintf
    // (v7a:0x19948) is "push {r0-r3}; add sp,sp,#16; bx lr": no effect.
}

JNIEXPORT void JNICALL
Java_com_sandlotgames_snailmail_ADRenderer_nativeDone(JNIEnv*, jobject) {
    // v7a:0x1555c: bl appDeinit (0x1568c: bx lr) ; b importGLDeinit (0x15498: bx lr).
    // Never invoked by the Java shell (original or port).
}

// ---------------------------------------------------------------- unreconstructed: fail loudly

JNIEXPORT void JNICALL
Java_com_sandlotgames_snailmail_ADGLSurfaceView_JNIKey(JNIEnv* env, jobject, jint) {
    SM_FAIL(Java_com_sandlotgames_snailmail_ADGLSurfaceView_JNIKey, "0x13d78");
}

JNIEXPORT void JNICALL
Java_com_sandlotgames_snailmail_AccelerometerListener_JNIAccelerometer(JNIEnv* env, jobject, jfloat, jfloat,
                                                                        jfloat) {
    SM_FAIL(Java_com_sandlotgames_snailmail_AccelerometerListener_JNIAccelerometer, "0x142bc");
}

JNIEXPORT void JNICALL
Java_com_sandlotgames_snailmail_SnailMailActivity_JNIResourceManagerInvalidate(JNIEnv* env, jclass) {
    SM_FAIL(Java_com_sandlotgames_snailmail_SnailMailActivity_JNIResourceManagerInvalidate, "0x142fc");
}

JNIEXPORT void JNICALL
Java_com_sandlotgames_snailmail_ADGLSurfaceView_JNIMouseEvent(JNIEnv* env, jclass, jint, jfloat, jfloat) {
    SM_FAIL(Java_com_sandlotgames_snailmail_ADGLSurfaceView_JNIMouseEvent, "0x14318");
}

// v7a:0x15244-0x15450. Opens the asset archive and indexes its directory.
// Behaviour kept from the original: the handle stays open for the process
// lifetime (JNIDatUnInit is a no-op); a failure leaves the archive closed and
// the Activity continues (the original only called the empty wprintf).
// Port differences: failures are logged at ERROR level; every size is
// validated before use; a repeated call replaces the previous handle instead
// of leaking it; the directory keeps offsets instead of patched 32-bit pointers.
JNIEXPORT void JNICALL
Java_com_sandlotgames_snailmail_SnailMailActivity_JNIDatInit(JNIEnv* env, jclass, jobject fd_obj, jint start,
                                                             jint length) {
    if (fd_obj == nullptr) {  // original: "cmpne r7,#0" -> message, return (v7a:0x152bc)
        __android_log_write(ANDROID_LOG_ERROR, kTag, "JNIDatInit: null FileDescriptor");
        return;
    }
    int fd = dup_java_fd(env, fd_obj);
    if (fd < 0) {
        __android_log_write(ANDROID_LOG_ERROR, kTag, "JNIDatInit: could not duplicate the asset fd");
        return;
    }
    sm_dat_close(&g_dat);
    sm_dat_status st = sm_dat_open(&g_dat, fd, start, length);
    if (st != SM_DAT_OK) {
        __android_log_print(ANDROID_LOG_ERROR, kTag,
                            "JNIDatInit: asm.mp3 rejected: %s (%s, record %u; start=%d length=%d)",
                            sm_dat_status_str(st), sm_asm_status_str(g_dat.asm_status),
                            g_dat.bad_index, start, length);
        return;
    }
    // Port diagnostic (log only): every name must resolve to itself or to an
    // earlier record with the same case-folded name (cRHash::Search semantics).
    unsigned self = 0, shadowed = 0, bad = 0;
    for (uint32_t i = 0; i < g_dat.dir.count; ++i) {
        const sm_asm_entry* e = &g_dat.dir.entries[i];
        const sm_asm_entry* hit = sm_dat_find(&g_dat, e->name);
        if (hit == e) {
            ++self;
        } else if (hit != nullptr && hit < e) {
            ++shadowed;
        } else {
            ++bad;
        }
    }
    __android_log_print(bad ? ANDROID_LOG_ERROR : ANDROID_LOG_INFO, kTag,
                        "JNIDatInit: asm.mp3 at offset %d, %d bytes: %u records, directory %zu bytes; "
                        "index self-check %u self, %u shadowed duplicate(s), %u FAILED",
                        start, length, g_dat.dir.count, g_dat.dir.dir_size, self, shadowed, bad);
}

JNIEXPORT void JNICALL
Java_com_sandlotgames_snailmail_ADRenderer_nativeRender(JNIEnv* env, jobject, jint) {
    SM_FAIL(Java_com_sandlotgames_snailmail_ADRenderer_nativeRender, "0x1549c");
}

JNIEXPORT void JNICALL
Java_com_sandlotgames_snailmail_ADGLSurfaceView_nativePause(JNIEnv* env, jclass) {
    SM_FAIL(Java_com_sandlotgames_snailmail_ADGLSurfaceView_nativePause, "0x15500");
}

JNIEXPORT void JNICALL
Java_com_sandlotgames_snailmail_ADRenderer_nativeResize(JNIEnv* env, jobject, jint, jint) {
    SM_FAIL(Java_com_sandlotgames_snailmail_ADRenderer_nativeResize, "0x1556c");
}

JNIEXPORT void JNICALL
Java_com_sandlotgames_snailmail_ADRenderer_nativeReInit(JNIEnv* env, jobject) {
    SM_FAIL(Java_com_sandlotgames_snailmail_ADRenderer_nativeReInit, "0x155f0");
}

JNIEXPORT void JNICALL
Java_com_sandlotgames_snailmail_ADRenderer_nativeInit(JNIEnv* env, jobject) {
    SM_FAIL(Java_com_sandlotgames_snailmail_ADRenderer_nativeInit, "0x15650");
}

JNIEXPORT void JNICALL
Java_com_sandlotgames_snailmail_ADRenderer_JNIOFOUnlockCB(JNIEnv* env, jobject, jint) {
    SM_FAIL(Java_com_sandlotgames_snailmail_ADRenderer_JNIOFOUnlockCB, "0x7d404");
}

JNIEXPORT void JNICALL
Java_com_sandlotgames_snailmail_ADRenderer_JNIOFOSubmitCB(JNIEnv* env, jobject, jint) {
    SM_FAIL(Java_com_sandlotgames_snailmail_ADRenderer_JNIOFOSubmitCB, "0x7d44c");
}

JNIEXPORT void JNICALL
Java_com_sandlotgames_snailmail_SnailMailActivity_JNIOFOSave(JNIEnv* env, jclass) {
    SM_FAIL(Java_com_sandlotgames_snailmail_SnailMailActivity_JNIOFOSave, "0x7d71c");
}

JNIEXPORT void JNICALL
Java_com_sandlotgames_snailmail_MyOpenFeintDelegate_JNIOFOInit(JNIEnv* env, jobject, jstring) {
    SM_FAIL(Java_com_sandlotgames_snailmail_MyOpenFeintDelegate_JNIOFOInit, "0x7d81c");
}

}  // extern "C"
