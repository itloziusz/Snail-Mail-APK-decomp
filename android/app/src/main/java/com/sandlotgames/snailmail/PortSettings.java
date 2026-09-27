package com.sandlotgames.snailmail;

import android.content.Context;
import android.content.SharedPreferences;
import android.view.Display;
import android.view.WindowManager;

/**
 * Port options (not part of the original APK): screen fit, field of view and
 * refresh mode. They are changed on the in-game Display page (native
 * aot/port/port_menu.c), persisted here in SharedPreferences, and pushed to
 * native code at start-up. Values match aot/port/port.h.
 */
final class PortSettings {
    static final int FIT_STRETCH = 0;
    static final int FIT_ADAPTIVE = 1;
    static final int FOV_ORIGINAL = 0;
    static final int FOV_ADAPTIVE = 1;
    static final int REFRESH_60 = 0;
    static final int REFRESH_120 = 1;
    static final int REFRESH_VRR = 2;

    /**
     * 120 Hz and VRR present frames interpolated between the game's 60 Hz
     * steps; offered only when the native frame interpolator is built in.
     */
    static final boolean INTERPOLATION_SUPPORTED = false;

    private static final String PREFS = "port";
    private static SnailMailActivity sActivity;
    static int fit = FIT_ADAPTIVE;
    static int fov = FOV_ADAPTIVE;
    static int refresh = REFRESH_60;
    private static int sModes = 1 << REFRESH_60;

    private PortSettings() {
    }

    // (IIII)V: fit, fov, refresh, bit mask of refresh modes this device can present.
    private static native void nativeSet(int fit, int fov, int refresh, int refreshModes);

    static void init(SnailMailActivity activity) {
        sActivity = activity;
        SharedPreferences p = activity.getSharedPreferences(PREFS, Context.MODE_PRIVATE);
        fit = p.getInt("fit", FIT_ADAPTIVE);
        fov = p.getInt("fov", FOV_ADAPTIVE);
        refresh = p.getInt("refresh", REFRESH_60);
        sModes = 1 << REFRESH_60;
        if (INTERPOLATION_SUPPORTED && maxRefreshRate(activity) >= 89.0f) {
            sModes |= (1 << REFRESH_120) | (1 << REFRESH_VRR);
        }
        if ((sModes & (1 << refresh)) == 0) {
            refresh = REFRESH_60;
        }
        nativeSet(fit, fov, refresh, sModes);
        applyRefresh();
    }

    /** Called by native code (GL thread) when the Display page changes a value. */
    static void onChanged(int newFit, int newFov, int newRefresh) {
        fit = newFit;
        fov = newFov;
        refresh = newRefresh;
        SnailMailActivity a = sActivity;
        if (a == null) {
            return;
        }
        a.getSharedPreferences(PREFS, Context.MODE_PRIVATE).edit()
                .putInt("fit", newFit).putInt("fov", newFov).putInt("refresh", newRefresh).apply();
        a.runOnUiThread(new Runnable() {
            @Override
            public void run() {
                applyRefresh();
            }
        });
    }

    private static float maxRefreshRate(SnailMailActivity a) {
        Display d = a.getWindowManager().getDefaultDisplay();
        Display.Mode cur = d.getMode();
        float max = cur.getRefreshRate();
        for (Display.Mode m : d.getSupportedModes()) {
            if (m.getPhysicalWidth() == cur.getPhysicalWidth()
                    && m.getPhysicalHeight() == cur.getPhysicalHeight()) {
                max = Math.max(max, m.getRefreshRate());
            }
        }
        return max;
    }

    /**
     * Display mode and presentation for the refresh setting (UI thread):
     * 60 Hz asks for the 60 Hz mode (the game assumes it; see ADGLSurfaceView),
     * 120 Hz asks for the 120 Hz mode, VRR leaves the choice to the system.
     */
    static void applyRefresh() {
        SnailMailActivity a = sActivity;
        if (a == null) {
            return;
        }
        float target = refresh == REFRESH_60 ? 60.0f : refresh == REFRESH_120 ? 120.0f : 0.0f;
        Display d = a.getWindowManager().getDefaultDisplay();
        Display.Mode cur = d.getMode();
        int modeId = 0; // 0: no preference
        if (target > 0.0f) {
            for (Display.Mode m : d.getSupportedModes()) {
                if (m.getPhysicalWidth() == cur.getPhysicalWidth()
                        && m.getPhysicalHeight() == cur.getPhysicalHeight()
                        && Math.abs(m.getRefreshRate() - target) < 1.0f) {
                    modeId = m.getModeId();
                }
            }
        }
        WindowManager.LayoutParams lp = a.getWindow().getAttributes();
        lp.preferredDisplayModeId = modeId;
        a.getWindow().setAttributes(lp);
        if (a.mGLView instanceof ADGLSurfaceView) {
            ((ADGLSurfaceView) a.mGLView).setPresentation(refresh);
        }
    }
}
