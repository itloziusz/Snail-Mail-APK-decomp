package android.hardware;
import java.util.Collections;
import java.util.List;
public class SensorManager {
    public static final int SENSOR_DELAY_FASTEST = 0;
    public final Sensor accel = new Sensor(Sensor.TYPE_ACCELEROMETER);
    public int starts, stops;
    public List<Sensor> getSensorList(int type) { return Collections.singletonList(accel); }
    public boolean registerListener(SensorEventListener listener, Sensor s, int rate) {
        if (rate != SENSOR_DELAY_FASTEST || s != accel) throw new AssertionError("sampling configuration");
        starts++; return true;
    }
    public void unregisterListener(SensorEventListener listener) { stops++; }
}
