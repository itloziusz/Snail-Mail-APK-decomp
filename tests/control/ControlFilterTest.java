package com.sandlotgames.snailmail;

/** Run with javac alongside the production ControlFilter, no Android runtime. */
public final class ControlFilterTest {
    private static void check(boolean ok, String message) {
        if (!ok) throw new AssertionError(message);
    }

    private static void near(float actual, float expected, float tolerance, String message) {
        check(Math.abs(actual - expected) <= tolerance,
                message + ": " + actual + " vs " + expected);
    }

    public static void main(String[] args) {
        // Independent transcription of original classes.dex onSensorChanged.
        ControlFilter filter = new ControlFilter();
        float lx = 0, ly = 0, lz = 0;
        for (int i = 0; i < 300; i++) {
            float x = (float)Math.sin(i * 0.61) * 9.8f;
            float y = (float)Math.cos(i * 0.37) * 9.8f;
            float z = 4.5f + (i % 7);
            double n = Math.sqrt(x*x + y*y + z*z);
            if (n == 0) n = 1;
            float nx = (float)(x/n), ny = (float)(y/n), nz = (float)(z/n);
            lx = lx + (nx-lx)*0.3f;
            ly = ly + (ny-ly)*0.3f;
            lz = lz + (nz-lz)*0.3f;
            int mode = i >= 100 && i < 200 ? ControlFilter.SMOOTH : ControlFilter.LEGACY;
            float[] out = filter.sample(x, y, z, i * 5_000_000L + 1, mode, 80);
            if (mode == ControlFilter.LEGACY) {
                check(Float.floatToRawIntBits(out[0]) == Float.floatToRawIntBits(-lx), "legacy x " + i);
                check(Float.floatToRawIntBits(out[1]) == Float.floatToRawIntBits(-ly), "legacy y " + i);
                check(Float.floatToRawIntBits(out[2]) == Float.floatToRawIntBits(lz), "legacy z " + i);
            }
        }

        // Same physical step at different sensor rates. Output is sampled at
        // a common physical time, independent of game frame rate.
        float at60 = stepAt(16_666_667L, 50);
        float at200 = stepAt(5_000_000L, 50);
        near(at60, at200, 0.04f, "sampling-rate invariance at 50 ms");
        check(at60 > 0.75f && at200 > 0.75f, "deliberate turn too slow");

        ControlFilter recenter = new ControlFilter();
        recenter.sample(0, 0, 10, 1, ControlFilter.SMOOTH, 100);
        recenter.sample(10, 0, 0, 50_000_001L, ControlFilter.SMOOTH, 100);
        float r = 0;
        for (int i = 1; i <= 12; ++i)
            r = recenter.sample(0, 0, 10, 50_000_001L + i*10_000_000L,
                    ControlFilter.SMOOTH, 100)[0];
        check(Math.abs(r) < 0.025f, "recenter drift/lag: " + r);

        float low = noiseAmplitude(10);
        float high = noiseAmplitude(100);
        check(high < low * 0.7f, "Smoothness has weak effect: " + low + ", " + high);
        ControlFilter edges = new ControlFilter();
        near(edges.sample(10, 0, 0, 1, ControlFilter.SMOOTH, 0)[0], -1, 0,
                "zero Smoothness bypass");
        check(edges.sample(Float.NaN, 0, 0, 2, ControlFilter.SMOOTH, 100) == null,
                "invalid Smooth sample must be rejected");
        check(edges.sample(0, 0, 0, 3, ControlFilter.SMOOTH, 100) == null,
                "zero gravity was treated as a steering orientation");
        check(edges.sample(Float.POSITIVE_INFINITY, 0, 0, 4, ControlFilter.SMOOTH, 100) == null,
                "infinite gravity was accepted");
        check(edges.sample(0, 10, 0, 1, ControlFilter.SMOOTH, 100) == null,
                "duplicate timestamp snapped input");
        check(edges.sample(0, 10, 0, 0, ControlFilter.SMOOTH, 100) == null,
                "out-of-order timestamp rewound input");
        near(edges.sample(0, 10, 0, 900_000_001L, ControlFilter.SMOOTH, 100)[1], -1, 0,
                "gap reset");
        landscapeReplay();
        System.out.println("ControlFilter: Legacy bit-exact 200 samples; rate, response, recenter and strength passed");
    }

    private static float roll(ControlFilter f, double degrees, long ns, int strength, int orientation) {
        double a = Math.toRadians(degrees);
        float x = (float)(orientation * 9.81 * Math.cos(a));
        float y = (float)(orientation * 9.81 * Math.sin(a));
        float[] v = f.sample(x,y,0,ns,ControlFilter.SMOOTH,strength);
        check(v != null, "valid landscape sample rejected");
        return (float)Math.toDegrees(Math.atan2(-orientation*v[1],-orientation*v[0]));
    }

    private static void landscapeReplay() {
        for (int orientation : new int[] {-1,1}) for (int hz : new int[] {60,100,200,400}) {
            ControlFilter f = new ControlFilter();
            float previous = roll(f,-30,1,100,orientation);
            float maxLag = 0;
            // Continuous left-to-right landscape roll: no false reversals.
            for (int i=1;i<=hz;++i) {
                float angle=-30+60f*i/hz;
                float actual=roll(f,angle,1+(long)(i*1e9/hz),100,orientation);
                check(actual >= previous-1e-4f && actual <= angle+1e-4f,
                        "roll reversed/overshot at " + hz + " Hz");
                maxLag=Math.max(maxLag,angle-actual);
                previous=actual;
            }
            check(maxLag<4, "landscape roll lag " + maxLag);
            // Reverse, then hold centered. No overshoot or persistent drift.
            for (int i=1;i<=hz;++i) {
                float angle=30-60f*i/hz;
                float actual=roll(f,angle,1+(long)((hz+i)*1e9/hz),100,orientation);
                check(Math.abs(actual)<=30.001f && (i<=Math.ceil(hz*0.06) || actual<=previous+1e-4f)
                                && (i<=Math.ceil(hz*0.06) || actual>=angle-1e-4f),
                        "reverse roll overshot at " + hz + " Hz");
                previous=actual;
            }
            for (int i=1;i<=hz/2;++i)
                previous=roll(f,0,1+(long)((2*hz+i)*1e9/hz),100,orientation);
            check(Math.abs(previous)<0.01f,"landscape recenter drift");
            // Fixed-frequency physical noise, rather than alternating once
            // per callback (which changes its physical frequency with Hz).
            f=new ControlFilter(); roll(f,0,1,100,orientation);
            double rawEnergy=0,filteredEnergy=0;
            for (int i=1;i<=hz*3;++i) {
                double t=(double)i/hz;
                double angle=1.5*Math.sin(2*Math.PI*12*t)+0.4*Math.sin(2*Math.PI*7*t);
                float actual=roll(f,angle,1+(long)(t*1e9),100,orientation);
                if(i>hz){rawEnergy+=angle*angle;filteredEnergy+=actual*actual;}
            }
            check(filteredEnergy<rawEnergy*0.25,"landscape jitter attenuation at "+hz+" Hz");
        }
        System.out.println("Landscape roll: both orientations; 60/100/200/400 Hz; reversals, <4 degree ramp lag, recenter, >50% RMS jitter reduction passed");
    }

    private static float stepAt(long period, int ms) {
        ControlFilter f = new ControlFilter();
        f.sample(0, 0, 10, 1, ControlFilter.SMOOTH, 100);
        float v = 0;
        for (long t = period; t <= ms*1_000_000L + period/2; t += period)
            v = f.sample(10, 0, 0, t+1, ControlFilter.SMOOTH, 100)[0];
        return Math.abs(v);
    }

    private static float noiseAmplitude(int smoothness) {
        ControlFilter f = new ControlFilter();
        f.sample(0, 0, 10, 1, ControlFilter.SMOOTH, smoothness);
        float sum = 0;
        for (int i = 1; i <= 300; ++i) {
            float x = (i % 2 == 0 ? 1f : -1f) * 0.3f;
            float v = f.sample(x, 0, 10, i*5_000_000L+1,
                    ControlFilter.SMOOTH, smoothness)[0];
            if (i > 100) sum += Math.abs(v);
        }
        return sum/200;
    }
}
