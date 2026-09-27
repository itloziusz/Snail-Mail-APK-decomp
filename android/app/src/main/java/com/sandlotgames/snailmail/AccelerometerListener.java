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
 * normalises the gravity vector, then smooths it by elapsed sensor time and
 * reports (-x, -y, z) to JNIAccelerometer (native v7a:0x142bc forwards to
 * cAccelerometer::Input when the game object exists).
 */
public class AccelerometerListener implements SensorEventListener {
    // A fixed per-callback factor changes feel with sensor sample rate. Use a
    // stable time constant, with a quicker response for deliberate large tilts.
    private static final float REST_TIME_CONSTANT_S = 0.070f;
    private static final float TURN_TIME_CONSTANT_S = 0.028f;
    private static final float LARGE_TILT_DELTA = 0.18f;
    private long lastSensorTimestamp;
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
            if (Float.isNaN(this.current_x) || Float.isInfinite(this.current_x)
                    || Float.isNaN(this.current_y) || Float.isInfinite(this.current_y)
                    || Float.isNaN(this.current_z) || Float.isInfinite(this.current_z)) {
                return;
            }
            long elapsedNs = event.timestamp - this.lastSensorTimestamp;
            if (this.lastSensorTimestamp == 0L || elapsedNs <= 0L || elapsedNs > 500_000_000L) {
                // Start or resume at the real orientation, without a slow ramp
                // from the zero vector or a stale orientation after a pause.
                this.last_x = this.current_x;
                this.last_y = this.current_y;
                this.last_z = this.current_z;
            } else {
                float dt = Math.min(elapsedNs * 1.0e-9f, 0.05f);
                float dx = this.current_x - this.last_x;
                float dy = this.current_y - this.last_y;
                float dz = this.current_z - this.last_z;
                float tau = dx * dx + dy * dy + dz * dz > LARGE_TILT_DELTA * LARGE_TILT_DELTA
                        ? TURN_TIME_CONSTANT_S : REST_TIME_CONSTANT_S;
                float alpha = (float) -Math.expm1(-dt / tau);
                this.last_x += dx * alpha;
                this.last_y += dy * alpha;
                this.last_z += dz * alpha;
            }
            this.lastSensorTimestamp = event.timestamp;
            JNIAccelerometer(-this.last_x, -this.last_y, this.last_z);
        }
    }
}
