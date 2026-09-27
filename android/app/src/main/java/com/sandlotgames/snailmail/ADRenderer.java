package com.sandlotgames.snailmail;

import android.content.Context;
import android.content.res.AssetFileDescriptor;
import android.graphics.Bitmap;
import android.graphics.BitmapFactory;
import android.media.MediaPlayer;
import android.opengl.GLSurfaceView;
import android.os.Vibrator;
import java.io.ByteArrayInputStream;
import java.io.FileInputStream;
import java.io.FileNotFoundException;
import java.io.FileOutputStream;
import java.io.IOException;
import java.nio.Buffer;
import java.nio.ByteBuffer;
import java.util.zip.ZipInputStream;
import javax.microedition.khronos.egl.EGLConfig;
import javax.microedition.khronos.opengles.GL10;

/**
 * Port of the original GLSurfaceView.Renderer (work/jadx/.../ADRenderer.java).
 *
 * The native library caches this object (JAVA_RegisterFunctions v7a:0x13ca4
 * stores the jobject in gJavaObj and GetObjectClass in gJavaClass) and
 * resolves the 26 methods below by name + signature from the table
 * gJAVAFunction (v7a:0x8b3f0; listed in docs/PLATFORM_BOUNDARIES.md). Names,
 * descriptors and public visibility must therefore not change.
 *
 * OpenFeint (defunct service, SDK not shipped with the port): the seven
 * JAVAOpenFeint* callbacks are kept with identical signatures but report the
 * truthful "service absent / no user / offline / request failed" state; they
 * never claim success. See PORT-CHANGE notes on each.
 */
class ADRenderer implements GLSurfaceView.Renderer {
    public static long JTime;
    private static boolean SurfaceCreatedFirstTime = false;
    private static boolean ActivityInitFlag = false;

    private native void JNIAudioInit();

    private native void nativeDone();

    private native void nativeInit();

    private native void nativeReInit();

    private native void nativeRender(int i);

    private native void nativeResize(int i, int i2);

    public native void JNIOFOSubmitCB(int i);

    public native void JNIOFOUnlockCB(int i);

    ADRenderer() {
    }

    @Override
    public void onSurfaceCreated(GL10 gl, EGLConfig config) {
        SnailMailActivity.ActivityInitFlag = false;
        if (!SurfaceCreatedFirstTime) {
            SnailMailActivity.wprintf("** OnSurfaceCreated");
            nativeInit();
            SurfaceCreatedFirstTime = true;
            SnailMailActivity.AudioInitFlag = false;
            return;
        }
        // Surface/context recreated (e.g. after onPause): native rebuilds GL state.
        SnailMailActivity.wprintf("** OnSurfaceCreated Again");
        nativeReInit();
        if (SnailMailActivity.AudioInitFlag) {
            SnailMailActivity.wprintf("+ReInit AudioInit");
            JNIAudioInit();
            SnailMailActivity.AudioInitFlag = false;
        }
    }

    @Override
    public void onSurfaceChanged(GL10 gl, int w, int h) {
        nativeResize(w, h);
    }

    @Override
    public void onDrawFrame(GL10 gl) {
        int SystemPauseFlag = 0;
        if (!SnailMailActivity.HasFocus) {
            SnailMailActivity.wprintf("Not got focus");
            SystemPauseFlag = 1;
        }
        nativeRender(SystemPauseFlag);
    }

    // [0] (Ljava/lang/String;)I
    public int JAVALoadSample(String SampleName) throws IOException {
        SnailMailActivity.wprintf(SampleName);
        try {
            AssetFileDescriptor fd = SnailMailActivity.getContext().getAssets().openFd(String.valueOf(SampleName) + ".ogg");
            int JSampleID = SnailMailActivity.Sp.load(fd.getFileDescriptor(), fd.getStartOffset(), fd.getLength(), 1);
            return JSampleID;
        } catch (Exception e) {
            SnailMailActivity.wprintf("***ERROR*** JAVALoadSample failed");
            return -1;
        }
    }

    // [1] (IF)I
    public int JAVAPlaySample(int SampleRef, float Volume) {
        int StreamID = SnailMailActivity.Sp.play(SampleRef, Volume, Volume, 1, 0, 1.0f);
        return StreamID;
    }

    // [2] (I)V
    public void JAVAStopSample(int StreamID) {
        SnailMailActivity.Sp.stop(StreamID);
    }

    // [3] (Ljava/lang/String;[BI)I -- app-private file (Context.openFileOutput, MODE_PRIVATE)
    public int JAVASaveFile(String FileName, byte[] Data, int length) throws IOException {
        try {
            SnailMailActivity.wprintf("JAVA Saving File " + FileName + " Length " + length);
            FileOutputStream FP = SnailMailActivity.getContext().openFileOutput(FileName, 0);
            FP.write(Data, 0, length);
            FP.close();
            return 1;
        } catch (Exception e) {
            SnailMailActivity.wprintf("***ERROR*** JAVASaveFile failed");
            return 0;
        }
    }

    // [4] (Ljava/lang/String;)V
    public void JAVADeleteFile(String FileName) {
        try {
            SnailMailActivity.getContext().deleteFile(FileName);
        } catch (Exception e) {
            SnailMailActivity.wprintf("***ERROR*** JAVADeleteFile failed");
        }
    }

    // [5] (Ljava/lang/String;[BI)V
    // TODO(port): FileInputStream.read may return fewer bytes than requested;
    // the original ignores the count. Kept identical for now.
    public void JAVALoadFile(String FileName, byte[] Data, int length) throws IOException {
        try {
            FileInputStream FP = SnailMailActivity.getContext().openFileInput(FileName);
            FP.read(Data, 0, length);
            FP.close();
        } catch (Exception e) {
            SnailMailActivity.wprintf("***ERROR*** JAVALoadFile failed");
        }
    }

    // [7] (Ljava/lang/String;)I
    public int JAVAFileSize(String FileName) throws IOException {
        try {
            FileInputStream FP = SnailMailActivity.getContext().openFileInput(FileName);
            int FileSize = FP.available();
            FP.close();
            return FileSize;
        } catch (Exception e) {
            SnailMailActivity.wprintf("***ERROR*** JAVAFileSize failed");
            return 0;
        }
    }

    // [6] (Ljava/lang/String;)I
    public int JAVAFindFile(String FileName) throws IOException {
        try {
            FileInputStream FP = SnailMailActivity.getContext().openFileInput(FileName);
            FP.close();
            return 1;
        } catch (FileNotFoundException e) {
            return 0;
        } catch (Exception e2) {
            return 0;
        }
    }

    // [8] (F)V
    public void JAVASetMusicVolume(float Volume) {
        SnailMailActivity.MusicVolume = Volume;
        if (SnailMailActivity.MusicPlayer != null) {
            SnailMailActivity.MusicPlayer.setVolume(Volume, Volume);
        }
    }

    // [9] (Ljava/lang/String;)V
    // TODO(port): prepare() runs synchronously on the GL thread as in the
    // original; audio focus is not requested (no API for it in 2011's flow).
    public void JAVAPlayMusic(String MusicName) throws IllegalStateException, IOException, IllegalArgumentException {
        SnailMailActivity.wprintf("JAVAPlayMusic + :" + MusicName + ":");
        try {
            AssetFileDescriptor fd = SnailMailActivity.getContext().getAssets().openFd(String.valueOf(MusicName) + ".ogg");
            SnailMailActivity.MusicPlayer = new MediaPlayer();
            SnailMailActivity.MusicPlayer.setDataSource(fd.getFileDescriptor(), fd.getStartOffset(), fd.getLength());
            SnailMailActivity.MusicPlayer.prepare();
            SnailMailActivity.MusicPlayer.setLooping(true);
            SnailMailActivity.MusicPlayer.setVolume(SnailMailActivity.MusicVolume, SnailMailActivity.MusicVolume);
            SnailMailActivity.MusicPlayer.start();
        } catch (Exception e) {
            SnailMailActivity.wprintf("JAVAPlayMusic Exception!! :" + MusicName + ":");
        }
        SnailMailActivity.wprintf("JAVAPlayMusic - :" + MusicName + ":");
    }

    // [10] ()V
    public void JAVAStopMusic() throws IllegalStateException {
        SnailMailActivity.wprintf("JAVAStopMusic +");
        if (SnailMailActivity.MusicPlayer != null) {
            SnailMailActivity.MusicPlayer.stop();
            SnailMailActivity.MusicPlayer.release();
            SnailMailActivity.MusicPlayer = null;
        }
        SnailMailActivity.wprintf("JAVAStopMusic -");
    }

    // [12] ()V
    public void JAVAPauseMusic() throws IllegalStateException {
        if (SnailMailActivity.MusicPlayer != null) {
            SnailMailActivity.MusicPlayer.pause();
        }
    }

    // [11] ()V
    public void JAVAUnPauseMusic() throws IllegalStateException {
        if (SnailMailActivity.MusicPlayer != null) {
            SnailMailActivity.MusicPlayer.start();
        }
    }

    // [13] ()V
    public void JAVAMusicRestart() throws IllegalStateException {
        if (SnailMailActivity.MusicPlayer != null) {
            SnailMailActivity.MusicPlayer.start();
        }
    }

    // [15] ([B[B)V -- decodes to ARGB_8888 and copies the Bitmap's raw
    // (premultiplied) RGBA bytes into Buffer, as the original.
    public void JAVAUnJpg(byte[] Buffer, byte[] BufferJpg) {
        SnailMailActivity.wprintf("UnJpg +");
        BitmapFactory.Options opt = new BitmapFactory.Options();
        opt.inDither = true;
        opt.inPreferredConfig = Bitmap.Config.ARGB_8888;
        Bitmap bmp = BitmapFactory.decodeByteArray(BufferJpg, 0, BufferJpg.length, opt);
        Buffer bbf = ByteBuffer.wrap(Buffer);
        bmp.copyPixelsToBuffer(bbf);
        SnailMailActivity.wprintf("UnJpg -");
    }

    // [16] ([B[B)V
    public void JAVAUnPng(byte[] Buffer, byte[] BufferPng) {
        SnailMailActivity.wprintf("UnPng +");
        BitmapFactory.Options opt = new BitmapFactory.Options();
        opt.inDither = true;
        opt.inPreferredConfig = Bitmap.Config.ARGB_8888;
        Bitmap bmp = BitmapFactory.decodeByteArray(BufferPng, 0, BufferPng.length, opt);
        Buffer bbf = ByteBuffer.wrap(Buffer);
        bmp.copyPixelsToBuffer(bbf);
        SnailMailActivity.wprintf("UnPng -");
    }

    // [14] ([B[B)V -- inflates the first zip entry into Buffer.
    public void JAVAUnZip(byte[] Buffer, byte[] BufferZip) throws IOException {
        int read;
        ByteArrayInputStream bin = new ByteArrayInputStream(BufferZip);
        ZipInputStream zin = new ZipInputStream(bin);
        byte[] TempBuffer = new byte[4096];
        try {
            zin.getNextEntry();
            int index = 0;
            do {
                read = zin.read(TempBuffer, 0, 4096);
                if (read != -1) {
                    for (int i = 0; i < read; i++) {
                        Buffer[index++] = TempBuffer[i];
                    }
                }
            } while (read != -1);
            zin.close();
        } catch (Exception e) {
            SnailMailActivity.wprintf("*** ERROR ****  JAVAUnZip Exception");
        }
    }

    // ---------------------------------------------------------------- OpenFeint
    // PORT-CHANGE (all JAVAOpenFeint*): the OpenFeint SDK and service no longer
    // exist. Each callback keeps its original descriptor and returns what the
    // original code path returns when no OpenFeint user is logged in / the
    // request fails. Nothing reports success.

    private static void openFeintRemoved(String what) {
        android.util.Log.w("SnailMail", "OpenFeint removed from port (defunct service): " + what);
    }

    // [17] ()V -- original: SnailMailActivity.JNIOFOSave(); Dashboard.open();
    public void JAVAOpenFeintOpen() {
        SnailMailActivity.JNIOFOSave(); // native part kept (saves of.cfg, v7a:0x7d71c -> OFOSave)
        openFeintRemoved("Dashboard.open() not available");
    }

    // [18] ([B)V -- original writes UserID[0]=0 when OpenFeint.getCurrentUser()==null.
    public void JAVAOpenFeintLastLoggedInUserID(byte[] UserID) {
        UserID[0] = 0;
    }

    // [19] (Ljava/lang/String;II)V -- original: async submit; onFailure -> JNIOFOSubmitCB(0).
    // The int is a native pointer in the original (docs/ABI_PORTING.md);
    // the port reports failure (0) on the GL thread and never echoes the value back.
    public void JAVAOpenFeintSubmit(String Leaderboard, int Score, final int CBOFOStatePtr) {
        SnailMailActivity.wprintf("Submit Leaderboard='" + Leaderboard + "' Score=" + Score);
        openFeintRemoved("score submit dropped; reporting failure via JNIOFOSubmitCB(0)");
        queueOnGlThread(new Runnable() {
            @Override
            public void run() {
                JNIOFOSubmitCB(0);
            }
        });
    }

    // [20] (Ljava/lang/String;I)V -- original: async unlock; onFailure -> JNIOFOUnlockCB(0).
    public void JAVAOpenFeintUnlock(String Achievement, final int CBOFOStatePtr) {
        SnailMailActivity.wprintf("Unlock Achievement='" + Achievement + "'");
        openFeintRemoved("achievement unlock dropped; reporting failure via JNIOFOUnlockCB(0)");
        queueOnGlThread(new Runnable() {
            @Override
            public void run() {
                JNIOFOUnlockCB(0);
            }
        });
    }

    // [21] ()I -- original: OpenFeint.isUserLoggedIn() ? 1 : 0
    public int JAVAOpenFeintIsUserLoggedIn() {
        return 0;
    }

    // [22] ()I -- original: OpenFeint.isNetworkConnected() ? 1 : 0
    public int JAVAOpenFeintIsOnline() {
        return 0;
    }

    private static void queueOnGlThread(Runnable r) {
        Context c = SnailMailActivity.getContext();
        if (c instanceof SnailMailActivity && ((SnailMailActivity) c).mGLView != null) {
            ((SnailMailActivity) c).mGLView.queueEvent(r);
        }
    }

    // ---------------------------------------------------------------- time
    // [23] ()I and [24] ()I -- native JAVATime() (v7a:0x14118) calls both and
    // forms (hi<<32|lo)/1000 (unsigned 64-bit, __aeabi_uldivmod): microseconds.
    public int JAVATime() {
        JTime = System.nanoTime();
        return (int) JTime;
    }

    public int JAVATimeHi() {
        return (int) (JTime >> 32);
    }

    // [25] (I)V
    public void JAVAVibrate(int MilliSecs) {
        Vibrator v = (Vibrator) SnailMailActivity.getContext().getSystemService(Context.VIBRATOR_SERVICE);
        v.vibrate(MilliSecs); // TODO(port): deprecated since API 26 (VibrationEffect)
    }
}
