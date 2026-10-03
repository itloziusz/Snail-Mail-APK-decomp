package android.hardware;
public class Sensor {
    public static final int TYPE_ACCELEROMETER = 1;
    private final int type;
    public Sensor(int type) { this.type = type; }
    public int getType() { return type; }
}
