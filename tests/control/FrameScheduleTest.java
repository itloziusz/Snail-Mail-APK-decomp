package com.sandlotgames.snailmail;

public final class FrameScheduleTest {
    public static void main(String[] args) {
        for (int hz : new int[] {60, 75, 90, 120, 144, 200}) {
            FrameSchedule s = new FrameSchedule();
            int renders = 0;
            for (int i = 0; i < hz * 10; i++) {
                if (s.shouldRender(Math.round(i * (1_000_000_000.0 / hz)) + 1, true)) renders++;
            }
            if (Math.abs(renders - 600) > 1)
                throw new AssertionError(hz + " Hz produced " + renders + " game ticks / 10 s");
        }
        FrameSchedule s = new FrameSchedule();
        for (int i = 0; i < 120; i++)
            if (!s.shouldRender(i * 8_333_333L + 1, false))
                throw new AssertionError("uncapped render missed");
        System.out.println("FrameSchedule: 60 ticks/s on 60, 75, 90, 120, 144, 200 Hz vsync; uncapped passed");
    }
}
