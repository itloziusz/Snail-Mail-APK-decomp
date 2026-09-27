package com.sandlotgames.snailmail;

import android.app.Activity;
import android.content.Context;
import android.content.res.AssetFileDescriptor;
import android.content.res.AssetManager;
import android.media.MediaPlayer;
import android.media.SoundPool;
import android.opengl.GLSurfaceView;
import android.os.Bundle;
import android.os.PowerManager;
import android.view.Display;
import android.view.WindowManager;
import java.io.FileDescriptor;
import java.io.IOException;

/**
 * Port of the original launcher Activity (work/jadx/.../SnailMailActivity.java,
 * smali work/apktool/smali/com/sandlotgames/snailmail/SnailMailActivity.smali).
 *
 * Native method names, modifiers and descriptors are unchanged so the JNI
 * symbols stay Java_com_sandlotgames_snailmail_SnailMailActivity_*.
 * Behaviour is kept identical except where marked PORT-CHANGE (with reason).
 * TODO markers list modern-lifecycle work that needs evidence/decisions first.
 */
public class SnailMailActivity extends Activity {
    public static AccelerometerListener Accelerometer;
    public static boolean ActivityInitFlag;
    public static boolean HasFocus;
    public static MediaPlayer MusicPlayer;
    public static SoundPool Sp;
    private static SnailMailActivity instance;
    public GLSurfaceView mGLView;
    private PowerManager.WakeLock wl;
    public static boolean AudioInitFlag = false;
    public static float MusicVolume = 0.0f;

    // (Ljava/io/FileDescriptor;II)V -- native reads the private int field
    // FileDescriptor.descriptor via GetFieldID (v7a:0x15244-0x152ec).
    // TODO(port): java.io.FileDescriptor.descriptor is a non-SDK field; the
    // reconstructed native side should instead receive the fd int (e.g.
    // ParcelFileDescriptor.getFd()) or use AAssetManager -- decide with the JNI owner.
    private static native void JNIDatInit(FileDescriptor fileDescriptor, int i, int i2);

    private static native void JNIDatUnInit();

    private static native int JNIDebug();

    public static native void JNIOFOSave();

    private static native void JNIResourceManagerInvalidate();

    public SnailMailActivity() {
        instance = this;
    }

    public static Context getContext() {
        return instance;
    }

    public static void wprintf(String Message) {
        // Original: prints only when JNIDebug()==1. The native JNIDebug returns
        // 0 (v7a:0x13b44 "mov r0,#0; bx lr"), so this is silent in the original.
        if (JNIDebug() == 1) {
            System.out.println(Message);
        }
    }

    @Override
    public void onCreate(Bundle savedInstanceState) {
        ActivityInitFlag = true;
        wprintf("*** OnCreate");
        wprintf("Snail Mail Start!");
        PowerManager pm = (PowerManager) getSystemService(Context.POWER_SERVICE);
        // 26 = SCREEN_BRIGHT_WAKE_LOCK | ON_AFTER_RELEASE, tag as in the original.
        // TODO(port): deprecated since API 17; FLAG_KEEP_SCREEN_ON is the modern equivalent.
        this.wl = pm.newWakeLock(26, "DoNotDimScreen");
        // PORT-CHANGE: the original calls super.onCreate() twice
        // (SnailMailActivity.smali lines 163 and 176: two invoke-super); on modern
        // Android the second call re-dispatches lifecycle callbacks, so it is
        // called once here, before the window-feature request as before.
        super.onCreate(savedInstanceState);
        requestWindowFeature(1); // Window.FEATURE_NO_TITLE
        getWindow().setFlags(1024, 1024); // WindowManager.LayoutParams.FLAG_FULLSCREEN
        this.mGLView = new ADGLSurfaceView(this);
        setContentView(this.mGLView);
        prefer60HzDisplayMode();
        AudioInitFlag = true;
        wprintf("Sfx Init");
        // 8 streams, AudioManager.STREAM_MUSIC (3), srcQuality 0 -- as original.
        // TODO(port): SoundPool(int,int,int) is deprecated (API 21); switch to
        // SoundPool.Builder + AudioAttributes(USAGE_GAME) and request audio focus.
        Sp = new SoundPool(8, 3, 0);
        AssetManager assetManager = getAssets();
        try {
            // Requires asm.mp3 to be STORED in the APK (noCompress in build.gradle.kts).
            AssetFileDescriptor Fd = assetManager.openFd("asm.mp3");
            int start = (int) Fd.getStartOffset();
            int length = (int) Fd.getLength();
            JNIDatInit(Fd.getFileDescriptor(), start, length);
        } catch (IOException e) {
            wprintf("asm.mp3 exception!!!");
        }
        Accelerometer = new AccelerometerListener(this);
        // TODO(port): the original never unregisters the listener (not even in onPause).
        Accelerometer.start();
    }

    /**
     * PORT-CHANGE: request the display's 60 Hz mode at the current resolution.
     * The game runs at least one 16,666 us update per drawn frame (appRender,
     * v7a:0x15690), so it assumes a 60 Hz display; see ADGLSurfaceView for the
     * other two parts of this change. No-op if the display has no 60 Hz mode.
     */
    private void prefer60HzDisplayMode() {
        Display display = getWindowManager().getDefaultDisplay();
        Display.Mode current = display.getMode();
        Display.Mode best = null;
        for (Display.Mode m : display.getSupportedModes()) {
            if (m.getPhysicalWidth() == current.getPhysicalWidth()
                    && m.getPhysicalHeight() == current.getPhysicalHeight()
                    && Math.abs(m.getRefreshRate() - 60.0f) < 1.0f) {
                best = m;
            }
        }
        if (best != null) {
            WindowManager.LayoutParams lp = getWindow().getAttributes();
            lp.preferredDisplayModeId = best.getModeId();
            getWindow().setAttributes(lp);
        }
    }

    @Override
    public void onDestroy() {
        wprintf("*** OnDestroy");
        super.onDestroy();
    }

    static {
        System.loadLibrary("snailmail");
    }

    @Override
    protected void onRestart() {
        Sp = new SoundPool(8, 3, 0);
        wprintf("*** OnRestart");
        super.onRestart();
    }

    @Override
    protected void onStart() {
        wprintf("*** OnStart");
        super.onStart();
    }

    @Override
    protected void onStop() {
        wprintf("*** OnStop");
        Sp.release();
        Sp = null;
        JNIDatUnInit();
        super.onStop();
    }

    @Override
    protected void onPause() {
        wprintf("*** OnPause");
        this.mGLView.onPause();
        this.wl.release();
        JNIResourceManagerInvalidate();
        if (MusicPlayer != null && MusicPlayer.isPlaying()) {
            MusicPlayer.pause();
        }
        // TODO(port): GLSurfaceView.onPause destroys the EGL context; the
        // original relies on onSurfaceCreated -> nativeReInit to rebuild GL
        // objects (ADRenderer). Verify on a modern device that
        // setPreserveEGLContextOnPause(false) (default) keeps that path.
        super.onPause();
    }

    @Override
    protected void onResume() {
        wprintf("*** OnResume");
        super.onResume();
        this.mGLView.onResume();
        this.wl.acquire();
    }

    @Override
    public void onWindowFocusChanged(boolean hasFocus) {
        HasFocus = hasFocus;
        super.onWindowFocusChanged(hasFocus);
    }
}
