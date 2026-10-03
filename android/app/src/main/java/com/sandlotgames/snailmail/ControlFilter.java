package com.sandlotgames.snailmail;

/** Sensor-vector processing before the game's unchanged JNI and steering code. */
final class ControlFilter {
    static final int LEGACY = 0;
    static final int SMOOTH = 1;
    private final float[] legacy = new float[3];
    private final float[] smooth = new float[3];
    private final float[] output = new float[3];
    private long smoothTime;
    private int previousMode = LEGACY;

    /** Returns (-x,-y,z), or null for an invalid Smooth sample. */
    float[] sample(float x, float y, float z, long timestampNs, int mode, int smoothness) {
        // Match the original DEX's float sum, double square root and float casts.
        double length = Math.sqrt(x * x + y * y + z * z);
        if (length == 0.0d) length = 1.0d;
        float nx = (float) (x / length);
        float ny = (float) (y / length);
        float nz = (float) (z / length);
        legacy[0] += (nx - legacy[0]) * 0.3f;
        legacy[1] += (ny - legacy[1]) * 0.3f;
        legacy[2] += (nz - legacy[2]) * 0.3f;
        if (mode == LEGACY) {
            previousMode = LEGACY;
            output[0] = -legacy[0]; output[1] = -legacy[1]; output[2] = legacy[2];
            return output;
        }
        // Smooth normalization uses a double norm and rejects missing gravity.
        // The legacy computation above is deliberately untouched.
        length = Math.sqrt((double)x*x + (double)y*y + (double)z*z);
        if (Double.isNaN(length) || Double.isInfinite(length) || length < 1.0e-6d) return null;
        nx = (float)(x/length); ny = (float)(y/length); nz = (float)(z/length);
        if (Float.isNaN(nx) || Float.isInfinite(nx) || Float.isNaN(ny) || Float.isInfinite(ny)
                || Float.isNaN(nz) || Float.isInfinite(nz)
                || length < 1.0e-6d) return null;
        // A timestamp-based one-pole filter. The short time constant for a
        // deliberate change keeps turns and recentering responsive at 100%.
        long elapsed = timestampNs - smoothTime;
        // Duplicate/out-of-order callbacks must not snap the filter to noise
        // or rewind its physical clock. Legacy keeps its original arithmetic.
        if (previousMode == SMOOTH && smoothTime != 0L && elapsed <= 0L) return null;
        if (smoothness <= 0 || previousMode != SMOOTH || smoothTime == 0L
                || elapsed > 500_000_000L) {
            smooth[0] = nx; smooth[1] = ny; smooth[2] = nz;
        } else {
            double dt = elapsed * 1.0e-9d;
            double strength = Math.max(0, Math.min(100, smoothness)) / 100.0d;
            double baseTau = 0.008d + 0.052d * strength;
            double dx = nx - smooth[0], dy = ny - smooth[1], dz = nz - smooth[2];
            double delta = Math.sqrt(dx * dx + dy * dy + dz * dz);
            double response = Math.min(1.0d, Math.max(0.0d, (delta - 0.04d) / 0.14d));
            double tau = baseTau * (1.0d - response) + Math.min(baseTau, 0.018d) * response;
            float alpha = (float) -Math.expm1(-dt / tau);
            smooth[0] += (float) dx * alpha;
            smooth[1] += (float) dy * alpha;
            smooth[2] += (float) dz * alpha;
        }
        previousMode = SMOOTH;
        smoothTime = timestampNs;
        output[0] = -smooth[0]; output[1] = -smooth[1]; output[2] = smooth[2];
        return output;
    }

    void resetSmoothTime() { smoothTime = 0L; }
}
