/*
 * Android glue for the AOT-translated game (shipping code, arm64-v8a).
 *
 *  - The 18 JNI exports of the original libsnailmail.so (same names, same
 *    Java descriptors). Each marshals its arguments into guest registers
 *    (AAPCS softfp: floats as bit patterns) and runs the translated original
 *    function via aot_invoke().
 *  - aot_java_ops on the real JNIEnv. Every object that enters the guest is
 *    held as a JNI *global* reference (the original cached raw local refs,
 *    docs/JNI_MAP.json hazard); objects are deduplicated with IsSameObject.
 *    java.io.FileDescriptor.descriptor (a non-SDK field) is served through
 *    public API: ParcelFileDescriptor.dup(fd).detachFd().
 *  - The GL backend is the device's real GLES 1.1 (libGLESv1_CM).
 */
#include <jni.h>
#include <android/log.h>
#include <stdlib.h>
#include <string.h>

#include "aot_host.h"
#include "aot_platform.h"

#ifdef SM_NDKLESS_GLES_DECLS
#include "sm_gles1_decls.h"
#else
#include <GLES/gl.h>
#endif

#define TAG "SnailMail"

/* ---------------------------------------------------------------- GL backend */
static void g_BufferData(sm_GLenum t, sm_GLsizeiptr s, const void *d, sm_GLenum u) { glBufferData(t, s, d, u); }
static const sm_gl_backend g_gles1 = {
    .BindBuffer = glBindBuffer, .BindTexture = glBindTexture, .BlendFunc = glBlendFunc,
    .BufferData = g_BufferData, .Clear = glClear, .ClearColor = glClearColor, .ClearDepthf = glClearDepthf,
    .Color4f = glColor4f, .CullFace = glCullFace, .DeleteTextures = glDeleteTextures, .DepthFunc = glDepthFunc,
    .DepthMask = glDepthMask, .DepthRangef = glDepthRangef, .Disable = glDisable,
    .DisableClientState = glDisableClientState, .DrawElements = glDrawElements, .Enable = glEnable,
    .EnableClientState = glEnableClientState, .Finish = glFinish, .Fogf = glFogf, .Fogfv = glFogfv,
    .Frustumf = glFrustumf, .GenBuffers = glGenBuffers, .GenTextures = glGenTextures, .Hint = glHint,
    .LineWidth = glLineWidth, .LoadIdentity = glLoadIdentity, .MatrixMode = glMatrixMode,
    .MultMatrixf = glMultMatrixf, .Orthof = glOrthof, .PixelStorei = glPixelStorei, .PopMatrix = glPopMatrix,
    .PushMatrix = glPushMatrix, .ReadPixels = glReadPixels, .Rotatef = glRotatef, .Scalef = glScalef,
    .Scissor = glScissor, .ShadeModel = glShadeModel, .TexCoordPointer = glTexCoordPointer,
    .TexEnvf = glTexEnvf, .TexImage2D = glTexImage2D, .TexParameteri = glTexParameteri,
    .Translatef = glTranslatef, .VertexPointer = glVertexPointer, .Viewport = glViewport,
};

/* ---------------------------------------------------------------- Java bridge */
static int g_fd_field_sentinel;

static void *to_global(JNIEnv *env, jobject local)
{
    jobject g;
    if (!local) return NULL;
    g = (*env)->NewGlobalRef(env, local);
    (*env)->DeleteLocalRef(env, local);
    return g;
}

static void clear_exception(JNIEnv *env, const char *where)
{
    if ((*env)->ExceptionCheck(env)) {
        __android_log_print(ANDROID_LOG_WARN, TAG, "Java exception during %s (cleared)", where);
        (*env)->ExceptionDescribe(env);
        (*env)->ExceptionClear(env);
    }
}

static void *op_find_class(void *ctx, const char *name)
{
    JNIEnv *env = (JNIEnv *)ctx;
    void *r = to_global(env, (*env)->FindClass(env, name));
    clear_exception(env, "FindClass");
    return r;
}
static void *op_get_object_class(void *ctx, void *obj)
{
    JNIEnv *env = (JNIEnv *)ctx;
    return to_global(env, (*env)->GetObjectClass(env, (jobject)obj));
}
static void *op_new_global_ref(void *ctx, void *obj)
{
    JNIEnv *env = (JNIEnv *)ctx;
    return obj ? (*env)->NewGlobalRef(env, (jobject)obj) : NULL;
}
static void op_delete_ref(void *ctx, void *obj)
{
    JNIEnv *env = (JNIEnv *)ctx;
    if (obj) (*env)->DeleteGlobalRef(env, (jobject)obj);
}
static int op_is_same_object(void *ctx, void *a, void *b)
{
    JNIEnv *env = (JNIEnv *)ctx;
    return (*env)->IsSameObject(env, (jobject)a, (jobject)b) ? 1 : 0;
}
static void *op_get_method_id(void *ctx, void *cls, const char *name, const char *sig)
{
    JNIEnv *env = (JNIEnv *)ctx;
    jmethodID m = (*env)->GetMethodID(env, (jclass)cls, name, sig);
    clear_exception(env, name);
    return (void *)m;
}
static void *op_get_field_id(void *ctx, void *cls, const char *name, const char *sig)
{
    JNIEnv *env = (JNIEnv *)ctx;
    jclass fdc = (*env)->FindClass(env, "java/io/FileDescriptor");
    int is_fd = fdc && (*env)->IsSameObject(env, (jobject)cls, fdc) && !strcmp(name, "descriptor") &&
                !strcmp(sig, "I");
    if (fdc) (*env)->DeleteLocalRef(env, fdc);
    if (is_fd) return &g_fd_field_sentinel;
    {
        jfieldID f = (*env)->GetFieldID(env, (jclass)cls, name, sig);
        clear_exception(env, name);
        return (void *)f;
    }
}
static int32_t op_get_int_field(void *ctx, void *obj, void *fid)
{
    JNIEnv *env = (JNIEnv *)ctx;
    if (fid == &g_fd_field_sentinel) {
        /* public API instead of the private field: an owned duplicate fd */
        jclass pfd = (*env)->FindClass(env, "android/os/ParcelFileDescriptor");
        jmethodID dup = (*env)->GetStaticMethodID(env, pfd, "dup",
                                                  "(Ljava/io/FileDescriptor;)Landroid/os/ParcelFileDescriptor;");
        jmethodID detach = (*env)->GetMethodID(env, pfd, "detachFd", "()I");
        jobject p = (*env)->CallStaticObjectMethod(env, pfd, dup, (jobject)obj);
        int fd = -1;
        if (!(*env)->ExceptionCheck(env) && p) fd = (*env)->CallIntMethod(env, p, detach);
        clear_exception(env, "ParcelFileDescriptor.dup");
        if (p) (*env)->DeleteLocalRef(env, p);
        (*env)->DeleteLocalRef(env, pfd);
        return fd;
    }
    return (*env)->GetIntField(env, (jobject)obj, (jfieldID)fid);
}
static aot_jvalue op_call_method(void *ctx, void *obj, void *mid, char ret, const aot_jvalue *a, int n)
{
    JNIEnv *env = (JNIEnv *)ctx;
    jvalue v[32];
    aot_jvalue r;
    int i;
    r.j = 0;
    for (i = 0; i < n && i < 32; ++i) memcpy(&v[i], &a[i], sizeof v[i] < sizeof a[i] ? sizeof v[i] : sizeof a[i]);
    /* aot_jvalue and jvalue are both 8-byte unions of the same members */
    if (ret == 'V') {
        (*env)->CallVoidMethodA(env, (jobject)obj, (jmethodID)mid, v);
    } else {
        r.i = (*env)->CallIntMethodA(env, (jobject)obj, (jmethodID)mid, v);
    }
    clear_exception(env, "callback");
    return r;
}
static void *op_new_string_utf(void *ctx, const char *s)
{
    JNIEnv *env = (JNIEnv *)ctx;
    return to_global(env, (*env)->NewStringUTF(env, s));
}
static char *op_get_string_utf_chars(void *ctx, void *str)
{
    JNIEnv *env = (JNIEnv *)ctx;
    const char *c = (*env)->GetStringUTFChars(env, (jstring)str, NULL);
    size_t len = c ? strlen(c) : 0;
    char *r = (char *)malloc(len + 1);
    if (r) memcpy(r, c ? c : "", len + 1);
    if (c) (*env)->ReleaseStringUTFChars(env, (jstring)str, c);
    return r;
}
static void *op_new_byte_array(void *ctx, int32_t len)
{
    JNIEnv *env = (JNIEnv *)ctx;
    return to_global(env, (*env)->NewByteArray(env, len));
}
static void op_get_region(void *ctx, void *arr, int32_t s, int32_t l, void *buf)
{
    JNIEnv *env = (JNIEnv *)ctx;
    (*env)->GetByteArrayRegion(env, (jbyteArray)arr, s, l, (jbyte *)buf);
    clear_exception(env, "GetByteArrayRegion");
}
static void op_set_region(void *ctx, void *arr, int32_t s, int32_t l, const void *buf)
{
    JNIEnv *env = (JNIEnv *)ctx;
    (*env)->SetByteArrayRegion(env, (jbyteArray)arr, s, l, (const jbyte *)buf);
    clear_exception(env, "SetByteArrayRegion");
}

static const aot_java_ops g_java = {
    op_find_class, op_get_object_class, op_new_global_ref, op_delete_ref, op_is_same_object,
    op_get_method_id, op_get_field_id, op_get_int_field, op_call_method, op_new_string_utf,
    op_get_string_utf_chars, op_new_byte_array, op_get_region, op_set_region,
};

/* ---------------------------------------------------------------- init + entry */
static void android_log(int level, const char *msg)
{
    int prio = level >= 7 ? ANDROID_LOG_FATAL : level >= 6 ? ANDROID_LOG_ERROR : level >= 5 ? ANDROID_LOG_WARN
             : level >= 4 ? ANDROID_LOG_INFO : ANDROID_LOG_DEBUG;
    __android_log_write(prio, TAG, msg);
}

static aot_lock g_init_lock = AOT_LOCK_INIT;

static aot_cpu *enter(JNIEnv *env)
{
    if (!aot_initialized()) {
        aot_lock_acquire(&g_init_lock);
        if (!aot_initialized()) {
            static aot_config cfg;
            cfg.java = &g_java;
            cfg.gl = &g_gles1;
            cfg.files_dir = NULL;
            cfg.log = android_log;
            aot_init(&cfg);
            __android_log_print(ANDROID_LOG_INFO, TAG, "AOT game code ready (%u translated functions)",
                                aot_func_count);
        }
        aot_lock_release(&g_init_lock);
    }
    return aot_thread_enter(env);
}

static uint32_t fbits(float f)
{
    uint32_t u;
    memcpy(&u, &f, 4);
    return u;
}

#define CALL(sym, ...)                                                                   \
    do {                                                                                 \
        static uint32_t addr_;                                                           \
        uint32_t a_[] = {__VA_ARGS__};                                                   \
        if (!addr_) addr_ = aot_symbol_addr(sym);                                        \
        if (!addr_) aot_fatal("translated entry point %s missing", sym);                 \
        ret_ = aot_invoke(c, addr_, a_, (int)(sizeof a_ / sizeof a_[0]), NULL);          \
    } while (0)

#define ENV_AND(obj) aot_thread_guest_env(), aot_handle_for_incoming(env, (obj))
#define PKG "Java_com_sandlotgames_snailmail_"

JNIEXPORT void JNICALL Java_com_sandlotgames_snailmail_SnailMailActivity_JNIDatInit(JNIEnv *env, jclass cls,
                                                                                  jobject fd, jint start,
                                                                                  jint len)
{
    aot_cpu *c = enter(env);
    uint32_t ret_;
    CALL(PKG "SnailMailActivity_JNIDatInit", ENV_AND(cls), aot_handle_for_incoming(env, fd), (uint32_t)start,
         (uint32_t)len);
    (void)ret_;
}
JNIEXPORT void JNICALL Java_com_sandlotgames_snailmail_SnailMailActivity_JNIDatUnInit(JNIEnv *env, jclass cls)
{
    aot_cpu *c = enter(env);
    uint32_t ret_;
    CALL(PKG "SnailMailActivity_JNIDatUnInit", ENV_AND(cls));
    (void)ret_;
}
JNIEXPORT jint JNICALL Java_com_sandlotgames_snailmail_SnailMailActivity_JNIDebug(JNIEnv *env, jclass cls)
{
    aot_cpu *c = enter(env);
    uint32_t ret_;
    CALL(PKG "SnailMailActivity_JNIDebug", ENV_AND(cls));
    return (jint)ret_;
}
JNIEXPORT void JNICALL Java_com_sandlotgames_snailmail_SnailMailActivity_JNIOFOSave(JNIEnv *env, jclass cls)
{
    aot_cpu *c = enter(env);
    uint32_t ret_;
    CALL(PKG "SnailMailActivity_JNIOFOSave", ENV_AND(cls));
    (void)ret_;
}
JNIEXPORT void JNICALL Java_com_sandlotgames_snailmail_SnailMailActivity_JNIResourceManagerInvalidate(JNIEnv *env,
                                                                                                  jclass cls)
{
    aot_cpu *c = enter(env);
    uint32_t ret_;
    CALL(PKG "SnailMailActivity_JNIResourceManagerInvalidate", ENV_AND(cls));
    (void)ret_;
}
JNIEXPORT void JNICALL Java_com_sandlotgames_snailmail_ADRenderer_JNIAudioInit(JNIEnv *env, jobject thiz)
{
    aot_cpu *c = enter(env);
    uint32_t ret_;
    CALL(PKG "ADRenderer_JNIAudioInit", ENV_AND(thiz));
    (void)ret_;
}
JNIEXPORT void JNICALL Java_com_sandlotgames_snailmail_ADRenderer_nativeInit(JNIEnv *env, jobject thiz)
{
    aot_cpu *c = enter(env);
    uint32_t ret_;
    CALL(PKG "ADRenderer_nativeInit", ENV_AND(thiz));
    (void)ret_;
}
JNIEXPORT void JNICALL Java_com_sandlotgames_snailmail_ADRenderer_nativeReInit(JNIEnv *env, jobject thiz)
{
    aot_cpu *c = enter(env);
    uint32_t ret_;
    CALL(PKG "ADRenderer_nativeReInit", ENV_AND(thiz));
    (void)ret_;
}
JNIEXPORT void JNICALL Java_com_sandlotgames_snailmail_ADRenderer_nativeDone(JNIEnv *env, jobject thiz)
{
    aot_cpu *c = enter(env);
    uint32_t ret_;
    CALL(PKG "ADRenderer_nativeDone", ENV_AND(thiz));
    (void)ret_;
}
JNIEXPORT void JNICALL Java_com_sandlotgames_snailmail_ADRenderer_nativeRender(JNIEnv *env, jobject thiz,
                                                                             jint pause)
{
    aot_cpu *c = enter(env);
    uint32_t ret_;
    CALL(PKG "ADRenderer_nativeRender", ENV_AND(thiz), (uint32_t)pause);
    (void)ret_;
}
JNIEXPORT void JNICALL Java_com_sandlotgames_snailmail_ADRenderer_nativeResize(JNIEnv *env, jobject thiz, jint w,
                                                                             jint h)
{
    aot_cpu *c = enter(env);
    uint32_t ret_;
    CALL(PKG "ADRenderer_nativeResize", ENV_AND(thiz), (uint32_t)w, (uint32_t)h);
    (void)ret_;
}
JNIEXPORT void JNICALL Java_com_sandlotgames_snailmail_ADRenderer_JNIOFOSubmitCB(JNIEnv *env, jobject thiz, jint p)
{
    aot_cpu *c = enter(env);
    uint32_t ret_;
    /* The int is a guest address (32-bit by construction), so the original
     * pointer-in-int contract stays valid in the AOT build. */
    CALL(PKG "ADRenderer_JNIOFOSubmitCB", ENV_AND(thiz), (uint32_t)p);
    (void)ret_;
}
JNIEXPORT void JNICALL Java_com_sandlotgames_snailmail_ADRenderer_JNIOFOUnlockCB(JNIEnv *env, jobject thiz, jint p)
{
    aot_cpu *c = enter(env);
    uint32_t ret_;
    CALL(PKG "ADRenderer_JNIOFOUnlockCB", ENV_AND(thiz), (uint32_t)p);
    (void)ret_;
}
JNIEXPORT void JNICALL Java_com_sandlotgames_snailmail_ADGLSurfaceView_JNIMouseEvent(JNIEnv *env, jclass cls,
                                                                                   jint action, jfloat x, jfloat y)
{
    aot_cpu *c = enter(env);
    uint32_t ret_;
    CALL(PKG "ADGLSurfaceView_JNIMouseEvent", ENV_AND(cls), (uint32_t)action, fbits(x), fbits(y));
    (void)ret_;
}
JNIEXPORT void JNICALL Java_com_sandlotgames_snailmail_ADGLSurfaceView_nativePause(JNIEnv *env, jclass cls)
{
    aot_cpu *c = enter(env);
    uint32_t ret_;
    CALL(PKG "ADGLSurfaceView_nativePause", ENV_AND(cls));
    (void)ret_;
}
JNIEXPORT void JNICALL Java_com_sandlotgames_snailmail_ADGLSurfaceView_JNIKey(JNIEnv *env, jobject thiz, jint key)
{
    aot_cpu *c = enter(env);
    uint32_t ret_;
    CALL(PKG "ADGLSurfaceView_JNIKey", ENV_AND(thiz), (uint32_t)key);
    (void)ret_;
}
JNIEXPORT void JNICALL Java_com_sandlotgames_snailmail_AccelerometerListener_JNIAccelerometer(JNIEnv *env,
                                                                                            jobject thiz, jfloat x,
                                                                                            jfloat y, jfloat z)
{
    aot_cpu *c = enter(env);
    uint32_t ret_;
    CALL(PKG "AccelerometerListener_JNIAccelerometer", ENV_AND(thiz), fbits(x), fbits(y), fbits(z));
    (void)ret_;
}
JNIEXPORT void JNICALL Java_com_sandlotgames_snailmail_MyOpenFeintDelegate_JNIOFOInit(JNIEnv *env, jobject thiz,
                                                                                    jstring user)
{
    aot_cpu *c = enter(env);
    uint32_t ret_;
    CALL(PKG "MyOpenFeintDelegate_JNIOFOInit", ENV_AND(thiz), aot_handle_for_incoming(env, user));
    (void)ret_;
}
