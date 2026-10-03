/*
 * Android side of the port options: com.sandlotgames.snailmail.PortSettings
 * (not part of the original APK) keeps the settings in SharedPreferences and
 * pushes them here; changes made on the in-game Display or Controls page are
 * sent back through PortSettings.onChanged(fit, fov, refresh, controls,
 * smoothness), which saves them and applies refresh changes on the UI thread.
 */
#include <jni.h>
#include <android/log.h>
#include <stdlib.h>

#include "port.h"

#define TAG "SnailMail"

JNIEXPORT void JNICALL Java_com_sandlotgames_snailmail_BackgroundArt_nativeAsset(
        JNIEnv *env, jclass cls, jint slot, jint w, jint h, jintArray pixels)
{
    (void)cls;
    if (!pixels || slot < 0 || slot >= 8 || w < 1 || h < 1 || w > 4096 || h > 4096
            || (*env)->GetArrayLength(env,pixels) != w*h) return;
    jint *argb = (*env)->GetIntArrayElements(env,pixels,NULL);
    if (!argb) return;
    unsigned char *rgba = malloc((size_t)w*h*4);
    if (rgba) {
        for (int i=0;i<w*h;++i) {
            unsigned v = (unsigned)argb[i];
            rgba[4*i]=v>>16; rgba[4*i+1]=v>>8; rgba[4*i+2]=v; rgba[4*i+3]=v>>24;
        }
        sm_port_splash_asset(slot,w,h,rgba); free(rgba);
    }
    (*env)->ReleaseIntArrayElements(env,pixels,argb,JNI_ABORT);
}

JNIEXPORT void JNICALL Java_com_sandlotgames_snailmail_BackgroundArt_nativeContextCreated(
        JNIEnv *env, jclass cls)
{
    (void)env; (void)cls; sm_port_splash_context_created();
}

JNIEXPORT void JNICALL Java_com_sandlotgames_snailmail_ADGLSurfaceView_nativeSafeArea(
        JNIEnv *env, jclass cls, jfloat l, jfloat t, jfloat r, jfloat b)
{
    (void)env; (void)cls;
    sm_port_safe_area(l, t, r, b);
}
JNIEXPORT jboolean JNICALL Java_com_sandlotgames_snailmail_ADGLSurfaceView_nativeNameEntryActive(
        JNIEnv *env, jclass cls)
{
    (void)env; (void)cls;
    return sm_port_nameentry_input_active() ? JNI_TRUE : JNI_FALSE;
}
JNIEXPORT jfloatArray JNICALL Java_com_sandlotgames_snailmail_NameEntryDebugView_nativeSnapshot(
        JNIEnv *env, jclass cls)
{
    float data[2 + 64 * 9];
    int n = sm_port_nameentry_debug(data, 2 + 64 * 9);
    jfloatArray out = (*env)->NewFloatArray(env, n);
    (void)cls;
    if (out) (*env)->SetFloatArrayRegion(env, out, 0, n, data);
    return out;
}

static JavaVM *g_vm;
static jclass g_cls;          /* global ref to PortSettings */
static jmethodID g_on_changed;

static void changed(const sm_port_settings *s, void *user)
{
    JNIEnv *env = NULL;
    (void)user;
    /* called on the GL thread inside nativeRender: already attached */
    if (!g_vm || !g_cls || (*g_vm)->GetEnv(g_vm, (void **)&env, JNI_VERSION_1_6) != JNI_OK || !env) {
        return;
    }
    (*env)->CallStaticVoidMethod(env, g_cls, g_on_changed, (jint)s->fit, (jint)s->fov,
                                 (jint)s->refresh, (jint)s->controls, (jint)s->smoothness);
    if ((*env)->ExceptionCheck(env)) {
        (*env)->ExceptionDescribe(env);
        (*env)->ExceptionClear(env);
    }
}

JNIEXPORT void JNICALL Java_com_sandlotgames_snailmail_PortSettings_nativeSet(JNIEnv *env, jclass cls, jint fit,
                                                                            jint fov, jint refresh,
                                                                            jint refresh_modes, jint controls,
                                                                            jint smoothness)
{
    sm_port_settings s;
    if (!g_cls) {
        (*env)->GetJavaVM(env, &g_vm);
        g_cls = (jclass)(*env)->NewGlobalRef(env, cls);
        g_on_changed = (*env)->GetStaticMethodID(env, cls, "onChanged", "(IIIII)V");
        sm_port_set_listener(changed, NULL);
    }
    sm_port_set_refresh_modes((unsigned)refresh_modes);
    s.fit = fit == SM_PORT_FIT_STRETCH ? SM_PORT_FIT_STRETCH : SM_PORT_FIT_ADAPTIVE;
    s.fov = fov == SM_PORT_FOV_ORIGINAL ? SM_PORT_FOV_ORIGINAL : SM_PORT_FOV_ADAPTIVE;
    s.refresh = (refresh >= 0 && refresh <= 2 && (sm_port_refresh_modes() & (1u << refresh))) ? refresh
                                                                                              : SM_PORT_REFRESH_60;
    s.controls = controls == SM_PORT_CONTROLS_SMOOTH ? SM_PORT_CONTROLS_SMOOTH : SM_PORT_CONTROLS_LEGACY;
    s.smoothness = smoothness < 0 ? 0 : smoothness > 100 ? 100 : smoothness;
    sm_port_set(&s);
    __android_log_print(ANDROID_LOG_INFO, TAG, "port settings: fit=%d fov=%d refresh=%d (modes 0x%x)", s.fit,
                        s.fov, s.refresh, (unsigned)refresh_modes);
}
