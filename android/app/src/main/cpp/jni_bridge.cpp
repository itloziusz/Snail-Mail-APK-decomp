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
// Entry points that fail loudly (unreconstructed):
//   ADGLSurfaceView.JNIKey 0x13d78, AccelerometerListener.JNIAccelerometer 0x142bc,
//   SnailMailActivity.JNIResourceManagerInvalidate 0x142fc,
//   ADGLSurfaceView.JNIMouseEvent 0x14318, SnailMailActivity.JNIDatInit 0x15244,
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

#include <cstdio>

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

JNIEXPORT void JNICALL
Java_com_sandlotgames_snailmail_SnailMailActivity_JNIDatInit(JNIEnv* env, jclass, jobject, jint, jint) {
    SM_FAIL(Java_com_sandlotgames_snailmail_SnailMailActivity_JNIDatInit, "0x15244");
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
