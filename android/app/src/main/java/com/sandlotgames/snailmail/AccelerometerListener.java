package com.sandlotgames.snailmail;

import android.app.Activity;
import android.content.Context;
import android.hardware.Sensor;
import android.hardware.SensorEvent;
import android.hardware.SensorEventListener;
import android.hardware.SensorManager;
import java.util.List;

/**
 * Port of the original AccelerometerListener (work/jadx/.../AccelerometerListener.java):
 * normalises the gravity vector, low-pass filters it with factor 0.3 and
 * reports (-x, -y, z) to JNIAccelerometer (native v7a:0x142bc forwards to
 * cAccelerometer::Input when the game object exists).
 */
public class AccelerometerListener implements SensorEventListener {
    private static final int FORCE_THRESHOLD = 900;
    private float currenForce;
    private float current_x;
    private float current_y;
    private float current_z;
    private float last_x;
    private float last_y;
    private float last_z;
    private Sensor sensor;
    private SensorManager sensorManager;
    private List<Sensor> sensors;
    private long lastUpdate = -1;
    private long currentTime = -1;
    private final int DATA_X = 0;
    private final int DATA_Y = 1;
    private final int DATA_Z = 2;

    // (FFF)V
    public native void JNIAccelerometer(float f, float f2, float f3);

    public AccelerometerListener(Activity parent) {
        this.sensorManager = (SensorManager) parent.getSystemService(Context.SENSOR_SERVICE);
        this.sensors = this.sensorManager.getSensorList(Sensor.TYPE_ACCELEROMETER);
        if (this.sensors.size() > 0) {
            this.sensor = this.sensors.get(0);
        }
    }

    public void start() {
        if (this.sensor != null) {
            // 0 = SENSOR_DELAY_FASTEST, as original. TODO(port): on API 31+ this
            // is capped at 200 Hz without HIGH_SAMPLING_RATE_SENSORS; the
            // callback runs on the main thread, concurrently with the GL thread.
            this.sensorManager.registerListener(this, this.sensor, 0);
        }
    }

    public void stop() {
        this.sensorManager.unregisterListener(this);
    }

    @Override
    public void onAccuracyChanged(Sensor s, int valu) {
    }

    @Override
    public void onSensorChanged(SensorEvent event) {
        if (event.sensor.getType() == 1 && event.values.length >= 3) {
            this.current_x = event.values[0];
            this.current_y = event.values[1];
            this.current_z = event.values[2];
            double n = Math.sqrt((this.current_x * this.current_x) + (this.current_y * this.current_y)
                    + (this.current_z * this.current_z));
            if (n == 0.0d) {
                n = 1.0d;
            }
            this.current_x = (float) (this.current_x / n);
            this.current_y = (float) (this.current_y / n);
            this.current_z = (float) (this.current_z / n);
            this.last_x += (this.current_x - this.last_x) * 0.3f;
            this.last_y += (this.current_y - this.last_y) * 0.3f;
            this.last_z += (this.current_z - this.last_z) * 0.3f;
            JNIAccelerometer(-this.last_x, -this.last_y, this.last_z);
        }
    }
}
