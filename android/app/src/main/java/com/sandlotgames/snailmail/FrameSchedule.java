package com.sandlotgames.snailmail;

/** Vsync selection for the game's fixed 60-step-per-second update. */
final class FrameSchedule {
    private static final long PERIOD_NS = 16_666_667L;
    private static final long JITTER_NS = 500_000L;
    private long nextDue;

    void reset() { nextDue = 0L; }

    boolean shouldRender(long vsyncNs, boolean capped) {
        if (!capped) return true;
        if (nextDue == 0L) {
            nextDue = vsyncNs + PERIOD_NS;
            return true;
        }
        if (vsyncNs + JITTER_NS < nextDue) return false;
        do { nextDue += PERIOD_NS; } while (nextDue <= vsyncNs);
        return true;
    }
}
