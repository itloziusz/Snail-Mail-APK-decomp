package com.sandlotgames.snailmail;
import android.hardware.SensorManager;
import java.util.ArrayList;
import java.util.List;
public class SnailMailActivity extends android.app.Activity {
    public final SensorManager manager = new SensorManager();
    public final FakeView mGLView = new FakeView();
    @Override public Object getSystemService(String name) { return manager; }
    public static class FakeView {
        public final List<Runnable> pending = new ArrayList<Runnable>();
        public void queueEvent(Runnable r) { pending.add(r); }
        public void flush() { while (!pending.isEmpty()) pending.remove(0).run(); }
    }
}
