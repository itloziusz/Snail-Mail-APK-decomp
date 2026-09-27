package com.sandlotgames.snailmail;

import android.app.Application;
import android.content.Context;

/**
 * Port of the original Application class (work/jadx/.../SnailMailApplication.java).
 *
 * PORT-CHANGE: the original onCreate() only built OpenFeintSettings and called
 * OpenFeint.initialize(...) with a MyOpenFeintDelegate; OpenFeint is defunct
 * and its SDK is not part of the port, so onCreate() does nothing else.
 * MyOpenFeintDelegate (whose only job was calling native JNIOFOInit on login)
 * is removed; Java_com_sandlotgames_snailmail_MyOpenFeintDelegate_JNIOFOInit
 * is therefore unreachable. The original's OpenFeint credentials are not copied.
 */
public class SnailMailApplication extends Application {
    private static SnailMailApplication instance;

    // Declared in the original but never invoked, and libsnailmail.so (both
    // builds) exports no Java_..._SnailMailApplication_JNIOFInit symbol.
    public static native void JNIOFInit();

    public SnailMailApplication() {
        instance = this;
    }

    public static Context getContext() {
        return instance;
    }

    @Override
    public void onCreate() {
        super.onCreate();
    }
}
