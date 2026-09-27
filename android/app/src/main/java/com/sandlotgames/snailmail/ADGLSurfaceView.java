package com.sandlotgames.snailmail;

import android.content.Context;
import android.opengl.GLSurfaceView;
import android.view.KeyEvent;
import android.view.MotionEvent;
import android.view.inputmethod.InputMethodManager;

/**
 * Port of the original ADGLSurfaceView (work/jadx/.../ADGLSurfaceView.java).
 * No setEGLContextClientVersion() call: GLSurfaceView's default is an
 * OpenGL ES 1.x context, matching the GLES 1.1 imports of libsnailmail.so.
 * Input mapping is unchanged: MotionEvent ACTION_DOWN(0)->0, ACTION_UP(1)->2,
 * ACTION_MOVE(2)->1; all other actions (incl. pointer up/down) ignored.
 */
class ADGLSurfaceView extends GLSurfaceView {
    ADRenderer mRenderer;

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
}
