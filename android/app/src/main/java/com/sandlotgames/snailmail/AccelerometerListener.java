package com.sandlotgames.snailmail;

import android.app.Activity;
import android.content.Context;
import android.hardware.Sensor;
import android.hardware.SensorEvent;
import android.hardware.SensorEventListener;
import android.hardware.SensorManager;

/** Samples the original accelerometer and feeds the unchanged native steering path. */
public class AccelerometerListener implements SensorEventListener {
    private final SensorManager manager;
    private final Sensor sensor;
    private final ControlFilter filter = new ControlFilter();
    private boolean registered;
    // One mailbox, not one queued Runnable per sensor callback. A delayed
    // frame consumes the newest filtered orientation, never an input backlog.
    private final Object sampleLock = new Object();
    private boolean pending;
    private float pendingX, pendingY, pendingZ;
    private int pendingEpoch;
    private int filterEpoch = -1;

    public native void JNIAccelerometer(float x, float y, float z);

    public AccelerometerListener(Activity parent) {
        manager = (SensorManager) parent.getSystemService(Context.SENSOR_SERVICE);
        java.util.List<Sensor> sensors = manager.getSensorList(Sensor.TYPE_ACCELEROMETER);
        sensor = sensors.isEmpty() ? null : sensors.get(0);
    }

    public void start() {
        if (sensor != null && !registered) {
            // Original DEX registers on the main looper at SENSOR_DELAY_FASTEST.
            registered = manager.registerListener(this, sensor, SensorManager.SENSOR_DELAY_FASTEST);
        }
    }

    public void stop() {
        if (registered) manager.unregisterListener(this);
        registered = false;
        filter.resetSmoothTime();
        synchronized (sampleLock) { pending = false; }
    }

    @Override public void onAccuracyChanged(Sensor sensor, int accuracy) { }

    @Override public void onSensorChanged(SensorEvent event) {
        if (event.sensor.getType() != Sensor.TYPE_ACCELEROMETER || event.values.length < 3) return;
        final int mode = PortSettings.controlsMode;
        final int epoch = PortSettings.controlEpoch.get();
        if (epoch != filterEpoch) {
            filter.resetSmoothTime();
            filterEpoch = epoch;
        }
        float[] v = filter.sample(event.values[0], event.values[1], event.values[2],
                event.timestamp, mode, PortSettings.smoothness);
        if (v == null) return;
        if (mode != PortSettings.controlsMode) return;
        final float x = v[0], y = v[1], z = v[2];
        if (mode == ControlFilter.LEGACY) {
            // Exact original callback order and JNI thread; no Smooth filtering.
            JNIAccelerometer(x, y, z);
        } else {
            synchronized (sampleLock) {
                pendingX = x; pendingY = y; pendingZ = z;
                pendingEpoch = epoch;
                pending = true;
            }
        }
    }

    /** Called only by the renderer immediately before a game update. */
    void consumeSmoothInput() {
        final float x, y, z;
        final int epoch;
        synchronized (sampleLock) {
            if (!pending) return;
            x = pendingX; y = pendingY; z = pendingZ;
            epoch = pendingEpoch;
            pending = false;
        }
        if (epoch == PortSettings.controlEpoch.get()
                && PortSettings.controlsMode == ControlFilter.SMOOTH)
            JNIAccelerometer(x, y, z);
    }
}
