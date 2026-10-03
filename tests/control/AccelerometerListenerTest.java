package com.sandlotgames.snailmail;

import android.hardware.SensorEvent;
import java.util.ArrayList;
import java.util.List;

/** Integration replay of the production listener with a fake Android sensor/GL loop. */
public final class AccelerometerListenerTest {
    private static class RecordingListener extends AccelerometerListener {
        final List<float[]> calls = new ArrayList<float[]>();
        RecordingListener(SnailMailActivity a) { super(a); }
        @Override public void JNIAccelerometer(float x, float y, float z) {
            calls.add(new float[] {x,y,z});
        }
    }
    private static void check(boolean p, String why) { if (!p) throw new AssertionError(why); }
    private static void near(float a, float b) { check(Math.abs(a-b) < 1e-6f, a + " != " + b); }

    public static void main(String[] args) {
        SnailMailActivity a = new SnailMailActivity();
        RecordingListener l = new RecordingListener(a);
        l.start(); l.start();
        check(a.manager.starts == 1, "double registration");
        l.onSensorChanged(new SensorEvent(a.manager.accel, new float[] {0,0,10}, 1));
        check(l.calls.size() == 1 && a.mGLView.pending.isEmpty(), "Legacy was queued");
        near(l.calls.get(0)[2], 0.3f);

        PortSettings.controlsMode = ControlFilter.SMOOTH;
        PortSettings.controlEpoch.incrementAndGet();
        l.onSensorChanged(new SensorEvent(a.manager.accel, new float[] {0,0,10}, 5_000_001L));
        check(l.calls.size() == 1 && a.mGLView.pending.isEmpty(), "Smooth created a callback backlog");
        l.consumeSmoothInput();
        near(l.calls.get(1)[2], 1f);
        l.consumeSmoothInput();
        check(l.calls.size() == 2, "sample was consumed twice");

        l.onSensorChanged(new SensorEvent(a.manager.accel, new float[] {10,0,0}, 10_000_001L));
        PortSettings.controlEpoch.incrementAndGet();
        l.consumeSmoothInput();
        check(l.calls.size() == 2, "stale queued input delivered");

        PortSettings.controlsMode = ControlFilter.LEGACY;
        l.onSensorChanged(new SensorEvent(a.manager.accel, new float[] {0,0,10}, 15_000_001L));
        near(l.calls.get(2)[2], 0.5499f); // Legacy state kept current while Smooth was selected.

        l.stop(); l.stop();
        check(a.manager.stops == 1, "double unregistration");
        l.start();
        check(a.manager.starts == 2, "failed to restart sensor");
        PortSettings.controlsMode = ControlFilter.SMOOTH;
        PortSettings.controlEpoch.incrementAndGet();
        // 1,000 events during a stalled frame must produce one latest sample.
        for (int i = 0; i < 1000; ++i)
            l.onSensorChanged(new SensorEvent(a.manager.accel,
                    new float[] {0,10,0}, 20_000_001L + i*1_000_000L));
        check(a.mGLView.pending.isEmpty(), "sensor burst queued work");
        l.consumeSmoothInput();
        check(l.calls.size() == 4, "sensor burst replayed old samples");
        near(l.calls.get(3)[1], -1f);
        l.onSensorChanged(new SensorEvent(a.manager.accel, new float[] {10,0,0}, 1_030_000_001L));
        l.stop();
        l.consumeSmoothInput();
        check(l.calls.size() == 4, "sample survived pause");
        System.out.println("AccelerometerListener: Legacy direct; latest sample, 1,000-event burst, stale discard, pause passed");
    }
}
