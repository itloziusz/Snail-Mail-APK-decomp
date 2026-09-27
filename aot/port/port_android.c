/*
 * Android side of the port options: com.sandlotgames.snailmail.PortSettings
 * (not part of the original APK) keeps the settings in SharedPreferences and
 * pushes them here; changes made on the in-game Display page are sent back
 * through PortSettings.onChanged(int fit, int fov, int refresh), which saves
 * them and applies the refresh mode on the UI thread.
 */
#include <jni.h>
#include <android/log.h>

#include "port.h"

#define TAG "SnailMail"

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
    (*env)->CallStaticVoidMethod(env, g_cls, g_on_changed, (jint)s->fit, (jint)s->fov, (jint)s->refresh);
    if ((*env)->ExceptionCheck(env)) {
        (*env)->ExceptionDescribe(env);
        (*env)->ExceptionClear(env);
    }
}

JNIEXPORT void JNICALL Java_com_sandlotgames_snailmail_PortSettings_nativeSet(JNIEnv *env, jclass cls, jint fit,
                                                                            jint fov, jint refresh,
                                                                            jint refresh_modes)
{
    sm_port_settings s;
    if (!g_cls) {
        (*env)->GetJavaVM(env, &g_vm);
        g_cls = (jclass)(*env)->NewGlobalRef(env, cls);
        g_on_changed = (*env)->GetStaticMethodID(env, cls, "onChanged", "(III)V");
        sm_port_set_listener(changed, NULL);
    }
    sm_port_set_refresh_modes((unsigned)refresh_modes);
    s.fit = fit == SM_PORT_FIT_STRETCH ? SM_PORT_FIT_STRETCH : SM_PORT_FIT_ADAPTIVE;
    s.fov = fov == SM_PORT_FOV_ORIGINAL ? SM_PORT_FOV_ORIGINAL : SM_PORT_FOV_ADAPTIVE;
    s.refresh = (refresh >= 0 && refresh <= 2 && (sm_port_refresh_modes() & (1u << refresh))) ? refresh
                                                                                              : SM_PORT_REFRESH_60;
    sm_port_set(&s);
    __android_log_print(ANDROID_LOG_INFO, TAG, "port settings: fit=%d fov=%d refresh=%d (modes 0x%x)", s.fit,
                        s.fov, s.refresh, (unsigned)refresh_modes);
}
