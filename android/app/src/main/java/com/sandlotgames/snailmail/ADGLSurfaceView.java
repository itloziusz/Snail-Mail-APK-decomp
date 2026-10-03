package com.sandlotgames.snailmail;

import android.content.Context;
import android.opengl.GLSurfaceView;
import android.os.Build;
import android.view.Choreographer;
import android.view.KeyEvent;
import android.view.MotionEvent;
import android.view.Surface;
import android.view.SurfaceHolder;
import android.view.WindowInsets;
import android.view.inputmethod.InputMethodManager;

/**
 * Port of the original ADGLSurfaceView (work/jadx/.../ADGLSurfaceView.java).
 * No setEGLContextClientVersion() call: GLSurfaceView's default is an
 * OpenGL ES 1.x context, matching the GLES 1.1 imports of libsnailmail.so.
 * Legacy gameplay retains the original MotionEvent mapping. Name entry and
 * Smooth gameplay track one pointer, handle cancellation, and queue writes
 * on the GL thread; name entry uses its own source-art coordinate transform.
 *
 * PORT-CHANGE: 60 Hz presentation. appRender (v7a:0x15690) runs at least one
 * 16,666 us simulation step for every frame GLSurfaceView draws
 * (docs/BOOT_CHAIN.md 3.2), so the original assumes a display of about 60 Hz.
 * On a 120 Hz display the game ran twice as fast. The game's timing code is
 * unchanged; the shell restores the frame rate it was written for:
 *   1. PortSettings asks the window for the 60 Hz display mode;
 *   2. surfaceCreated() declares the surface as fixed-rate 60 fps content
 *      (Surface.setFrameRate, Android 11+);
 *   3. FramePacer selects vsyncs on a 60 Hz deadline schedule even if the
 *      system keeps a faster mode (including 75/90 Hz panels).
 * On a 60 Hz display every vsync is drawn, as in the original. The 120 Hz and
 * VRR port options (PortSettings) draw every vsync instead; native code then
 * interpolates between the game's 60 Hz steps.
 */
class ADGLSurfaceView extends GLSurfaceView {
    ADRenderer mRenderer;
    private int activeTouchId = -1;
    private float activeTouchX, activeTouchY;

    // Surface.FRAME_RATE_COMPATIBILITY_FIXED_SOURCE (API 30).
    private static final int FRAME_RATE_COMPATIBILITY_FIXED_SOURCE = 1;

    private final FramePacer mPacer = new FramePacer();
    private int mRefresh = PortSettings.REFRESH_60;

    // (IFF)V -- native scales x by 640/deviceWidth, y by 480/deviceHeight
    // (v7a:0x14318-0x14370).
    private static native void JNIMouseEvent(int i, float f, float f2);

    // Declared but never invoked by the original Java code (smali: declaration only).
    private static native void nativePause();

    public native void JNIKey(int i);
    private static native boolean nativeNameEntryActive();
    private static native void nativeSafeArea(float l, float t, float r, float b);

    @Override public WindowInsets onApplyWindowInsets(WindowInsets insets) {
        // The GL surface may already be inset by Android. Convert window
        // obscured edges to surface-local distances to avoid double insets.
        int l = insets.getSystemWindowInsetLeft(), t = insets.getSystemWindowInsetTop();
        int r = insets.getSystemWindowInsetRight(), b = insets.getSystemWindowInsetBottom();
        if (Build.VERSION.SDK_INT >= 28) {
            try {
                Object cutout = WindowInsets.class.getMethod("getDisplayCutout").invoke(insets);
                if (cutout != null) {
                    Class<?> c = Class.forName("android.view.DisplayCutout");
                    l = Math.max(l, (Integer)c.getMethod("getSafeInsetLeft").invoke(cutout));
                    t = Math.max(t, (Integer)c.getMethod("getSafeInsetTop").invoke(cutout));
                    r = Math.max(r, (Integer)c.getMethod("getSafeInsetRight").invoke(cutout));
                    b = Math.max(b, (Integer)c.getMethod("getSafeInsetBottom").invoke(cutout));
                }
            } catch (ReflectiveOperationException e) { throw new IllegalStateException(e); }
        }
        final int il = l, it = t, ir = r, ib = b;
        post(new Runnable() { @Override public void run() {
            int[] surface = new int[2], root = new int[2];
            getLocationInWindow(surface); getRootView().getLocationInWindow(root);
            final float sl = Math.max(0, il - (surface[0] - root[0]));
            final float st = Math.max(0, it - (surface[1] - root[1]));
            final float sr = Math.max(0, ir - (getRootView().getWidth() - getWidth() - surface[0] + root[0]));
            final float sb = Math.max(0, ib - (getRootView().getHeight() - getHeight() - surface[1] + root[1]));
            queueEvent(new Runnable() { @Override public void run() { nativeSafeArea(sl, st, sr, sb); }});
        }});
        return insets;
    }

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
        if (keycode == KeyEvent.KEYCODE_BACK && event.getRepeatCount() == 0) return true;
        final int key = keycode;
        boolean text = (key >= 29 && key <= 54) || (key >= 7 && key <= 16)
                || key == KeyEvent.KEYCODE_SPACE || key == KeyEvent.KEYCODE_DEL
                || key == KeyEvent.KEYCODE_ENTER;
        boolean navigation = (key >= 19 && key <= 23) || key == KeyEvent.KEYCODE_BUTTON_A;
        if (text || (navigation && nativeNameEntryActive())) {
            if (key == KeyEvent.KEYCODE_ENTER) {
                InputMethodManager imm = (InputMethodManager)getContext()
                        .getSystemService(Context.INPUT_METHOD_SERVICE);
                imm.hideSoftInputFromWindow(getWindowToken(), 0);
            }
            // Text, selection and touch share the GL update's FIFO. Consuming
            // handled events prevents a second platform focus/navigation action.
            queueEvent(new Runnable() { @Override public void run() { JNIKey(key); }});
            return true;
        }
        return super.onKeyDown(keycode, event);
    }

    @Override
    public boolean onTouchEvent(MotionEvent event) {
        if (nativeNameEntryActive() || PortSettings.controlsMode == PortSettings.CONTROLS_SMOOTH)
            return onSmoothTouchEvent(event);
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

    private void sendSmoothTouch(final int action, final float x, final float y) {
        // cRMouse is consumed by the GL update. Keep touch writes on that
        // thread. The newest Smooth orientation is consumed before rendering.
        final int epoch = PortSettings.controlEpoch.get();
        queueEvent(new Runnable() {
            @Override public void run() {
                if (epoch == PortSettings.controlEpoch.get()) JNIMouseEvent(action, x, y);
            }
        });
    }

    void cancelSmoothTouch() {
        if (activeTouchId >= 0) sendSmoothTouch(2, activeTouchX, activeTouchY);
        activeTouchId = -1;
    }

    private boolean onSmoothTouchEvent(MotionEvent event) {
        int action = event.getActionMasked();
        if (action == MotionEvent.ACTION_DOWN) {
            activeTouchId = event.getPointerId(0);
            activeTouchX = event.getX(0);
            activeTouchY = event.getY(0);
            sendSmoothTouch(0, activeTouchX, activeTouchY);
        } else if (action == MotionEvent.ACTION_MOVE) {
            int index = event.findPointerIndex(activeTouchId);
            if (index >= 0) {
                activeTouchX = event.getX(index);
                activeTouchY = event.getY(index);
                sendSmoothTouch(1, activeTouchX, activeTouchY);
            }
        } else if (action == MotionEvent.ACTION_UP || action == MotionEvent.ACTION_CANCEL
                || action == MotionEvent.ACTION_POINTER_UP
                && event.getPointerId(event.getActionIndex()) == activeTouchId) {
            int index = event.findPointerIndex(activeTouchId);
            if (index >= 0) {
                activeTouchX = event.getX(index);
                activeTouchY = event.getY(index);
            }
            // A menu press can switch Legacy -> Smooth between DOWN and UP.
            if (activeTouchId < 0 && event.getPointerCount() > 0) {
                activeTouchX = event.getX(0);
                activeTouchY = event.getY(0);
            }
            sendSmoothTouch(2, activeTouchX, activeTouchY);
            activeTouchId = -1;
        }
        return true;
    }

    @Override
    public void surfaceCreated(SurfaceHolder holder) {
        super.surfaceCreated(holder);
        applyFrameRate(holder.getSurface());
    }

    /** PortSettings.REFRESH_*; UI thread. */
    void setPresentation(int refresh) {
        mRefresh = refresh;
        mPacer.mCapped = refresh == PortSettings.REFRESH_60;
        mPacer.mSchedule.reset();
        Surface s = getHolder().getSurface();
        if (s != null && s.isValid()) {
            applyFrameRate(s);
        }
    }

    // Surface.setFrameRate(float, int) is API 30; the shell compiles against
    // android-23, so it is looked up by reflection. Best effort: at 60 Hz the
    // FramePacer still limits presentation. 0 clears the vote (VRR: the system
    // chooses).
    private void applyFrameRate(Surface surface) {
        if (Build.VERSION.SDK_INT < 30) {
            return;
        }
        float rate = mRefresh == PortSettings.REFRESH_60 ? 60.0f
                : mRefresh == PortSettings.REFRESH_120 ? 120.0f : 0.0f;
        try {
            Surface.class.getMethod("setFrameRate", float.class, int.class)
                    .invoke(surface, rate, FRAME_RATE_COMPATIBILITY_FIXED_SOURCE);
        } catch (Exception e) {
            // ignored, see above
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

    /** Selects vsyncs to average 60 game ticks/s on 60, 75, 90, 120 Hz panels. */
    private final class FramePacer implements Choreographer.FrameCallback {
        private boolean mRunning;
        private final FrameSchedule mSchedule = new FrameSchedule();
        boolean mCapped = true;

        void start() {
            if (mRunning) {
                return;
            }
            mRunning = true;
            mSchedule.reset();
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
            if (mSchedule.shouldRender(frameTimeNanos, mCapped)) {
                requestRender();
            }
            Choreographer.getInstance().postFrameCallback(this);
        }
    }
}
