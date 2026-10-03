package android.hardware;
public class SensorEvent {
    public Sensor sensor;
    public float[] values;
    public long timestamp;
    public SensorEvent(Sensor s, float[] v, long t) { sensor = s; values = v; timestamp = t; }
}
