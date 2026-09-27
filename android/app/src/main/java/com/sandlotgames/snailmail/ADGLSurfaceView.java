package com.sandlotgames.snailmail;

import android.content.Context;
import android.opengl.GLSurfaceView;
import android.os.Build;
import android.view.Choreographer;
import android.view.KeyEvent;
import android.view.MotionEvent;
import android.view.Surface;
import android.view.SurfaceHolder;
import android.view.inputmethod.InputMethodManager;

/**
 * Port of the original ADGLSurfaceView (work/jadx/.../ADGLSurfaceView.java).
 * No setEGLContextClientVersion() call: GLSurfaceView's default is an
 * OpenGL ES 1.x context, matching the GLES 1.1 imports of libsnailmail.so.
 * Input mapping is unchanged: MotionEvent ACTION_DOWN(0)->0, ACTION_UP(1)->2,
 * ACTION_MOVE(2)->1; all other actions (incl. pointer up/down) ignored.
 *
 * PORT-CHANGE: 60 Hz presentation. appRender (v7a:0x15690) runs at least one
 * 16,666 us simulation step for every frame GLSurfaceView draws
 * (docs/BOOT_CHAIN.md 3.2), so the original assumes a display of about 60 Hz.
 * On a 120 Hz display the game ran twice as fast. The game's timing code is
 * unchanged; the shell restores the frame rate it was written for:
 *   1. SnailMailActivity asks the window for the 60 Hz display mode;
 *   2. surfaceCreated() declares the surface as fixed-rate 60 fps content
 *      (Surface.setFrameRate, Android 11+);
 *   3. FramePacer draws on vsync, but never twice within MIN_FRAME_INTERVAL_NS,
 *      in case the system keeps a faster refresh rate anyway.
 * On a 60 Hz display every vsync is drawn, as in the original.
 */
class ADGLSurfaceView extends GLSurfaceView {
    ADRenderer mRenderer;

    // One 60 Hz period minus 2 ms of tolerance for vsync timestamp jitter.
    // Below half of a 120 Hz vsync period (4.17 ms), so on a 120 Hz display
    // exactly every second vsync is drawn.
    private static final long MIN_FRAME_INTERVAL_NS = 16666667L - 2000000L;
    // Surface.FRAME_RATE_COMPATIBILITY_FIXED_SOURCE (API 30).
    private static final int FRAME_RATE_COMPATIBILITY_FIXED_SOURCE = 1;

    private final FramePacer mPacer = new FramePacer();

    // (IFF)V -- native scales x by 640/deviceWidth, y by 480/deviceHeight
    // (v7a:0x14318-0x14370).
    private static native void JNIMouseEvent(int i, float f, float f2);

    // Declared but never invoked by the original Java code (smali: declaration only).
    private static native void nativePause();

    public native void JNIKey(int i);

    public ADGLSurfaceView(Context context) {
        super(context);
        this.mRenderer = new ADRenderer();
        // TODO(port): EGL config is GLSurfaceView's default chooser (as original);
        // verify depth-buffer size on modern devices (docs/PLATFORM_BOUNDARIES.md).
        setRenderer(this.mRenderer);
        // PORT-CHANGE: frames are requested by FramePacer (see class comment);
        // the original used GLSurfaceView's default RENDERMODE_CONTINUOUSLY.
        setRenderMode(RENDERMODE_WHEN_DIRTY);
        requestFocus();
        setFocusableInTouchMode(true);
    }

    @Override
    public boolean onKeyDown(int keycode, KeyEvent event) {
        SnailMailActivity.wprintf("OnKeyDown " + keycode);
        if (keycode == 4 && event.getRepeatCount() == 0) {
            // KEYCODE_BACK swallowed, as original.
            SnailMailActivity.wprintf("Back Pressed");
            return true;
        }
        switch (event.getAction()) {
            case 0:
                if (keycode == 19) {
                    SnailMailActivity.wprintf("DPAD_UP");
                }
                if (keycode == 20) {
                    SnailMailActivity.wprintf("DPAD_DOWN");
                }
                if (keycode == 21) {
                    SnailMailActivity.wprintf("DPAD_LEFT");
                }
                if (keycode == 22) {
                    SnailMailActivity.wprintf("DPAD_RIGHT");
                }
                if (keycode >= 29 && keycode <= 54) {
                    SnailMailActivity.wprintf("Key A-Z " + (keycode - 29));
                    JNIKey(keycode);
                }
                if (keycode >= 7 && keycode <= 16) {
                    SnailMailActivity.wprintf("Key 0-9 " + (keycode - 7));
                    JNIKey(keycode);
                }
                if (keycode == 62) {
                    JNIKey(keycode);
                }
                if (keycode == 67) {
                    SnailMailActivity.wprintf("Key Delete " + keycode);
                    JNIKey(keycode);
                }
                if (keycode == 66) {
                    InputMethodManager imm = (InputMethodManager) SnailMailActivity.getContext()
                            .getSystemService(Context.INPUT_METHOD_SERVICE);
                    imm.hideSoftInputFromWindow(getWindowToken(), 0);
                    JNIKey(keycode);
                    break;
                }
                break;
            default:
                break;
        }
        return super.onKeyDown(keycode, event);
    }

    @Override
    public boolean onTouchEvent(MotionEvent event) {
        int action = event.getAction();
        // TODO(port): called on the UI thread while the renderer runs on the
        // GL thread, as in the original; the native side writes shared globals
        // without locking (docs/PLATFORM_BOUNDARIES.md, input).
        switch (action) {
            case 0:
                JNIMouseEvent(0, event.getX(), event.getY());
                return true;
            case 1:
                JNIMouseEvent(2, event.getX(), event.getY());
                return true;
            case 2:
                JNIMouseEvent(1, event.getX(), event.getY());
                return true;
            default:
                return true;
        }
    }

    @Override
    public void surfaceCreated(SurfaceHolder holder) {
        super.surfaceCreated(holder);
        // Surface.setFrameRate(float, int) is API 30; the shell compiles
        // against android-23, so it is looked up by reflection.
        if (Build.VERSION.SDK_INT >= 30) {
            try {
                Surface.class.getMethod("setFrameRate", float.class, int.class)
                        .invoke(holder.getSurface(), 60.0f, FRAME_RATE_COMPATIBILITY_FIXED_SOURCE);
            } catch (Exception e) {
                // Best effort: FramePacer still limits presentation to 60 Hz.
            }
        }
    }

    @Override
    public void onResume() {
        super.onResume();
        mPacer.start();
    }

    @Override
    public void onPause() {
        mPacer.stop();
        super.onPause();
    }

    /** Requests one render per vsync, at most one per MIN_FRAME_INTERVAL_NS. Main thread only. */
    private final class FramePacer implements Choreographer.FrameCallback {
        private boolean mRunning;
        private long mLastFrameNs;

        void start() {
            if (mRunning) {
                return;
            }
            mRunning = true;
            mLastFrameNs = 0;
            Choreographer.getInstance().postFrameCallback(this);
        }

        void stop() {
            mRunning = false;
            Choreographer.getInstance().removeFrameCallback(this);
        }

        @Override
        public void doFrame(long frameTimeNanos) {
            if (!mRunning) {
                return;
            }
            if (mLastFrameNs == 0 || frameTimeNanos - mLastFrameNs >= MIN_FRAME_INTERVAL_NS) {
                mLastFrameNs = frameTimeNanos;
                requestRender();
            }
            Choreographer.getInstance().postFrameCallback(this);
        }
    }
}
