#!/usr/bin/env python3
"""Compile actual Android control classes against minimal host sensor stubs."""
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[2]
HERE = Path(__file__).resolve().parent
JAVA = ROOT / "android/app/src/main/java/com/sandlotgames/snailmail"


def main() -> int:
    if not shutil.which("javac") or not shutil.which("java"):
        print("SKIP: Java compiler/runtime unavailable")
        return 77
    sources = [str(p) for p in (HERE / "stubs").rglob("*.java")]
    sources += [str(JAVA / name) for name in (
        "ControlFilter.java", "FrameSchedule.java", "AccelerometerListener.java")]
    sources += [str(HERE / name) for name in (
        "ControlFilterTest.java", "FrameScheduleTest.java", "AccelerometerListenerTest.java")]
    with tempfile.TemporaryDirectory(prefix="snailmail-controls-") as out:
        subprocess.run(["javac", "-d", out, *sources], check=True)
        for name in ("ControlFilterTest", "FrameScheduleTest", "AccelerometerListenerTest"):
            subprocess.run(["java", "-cp", out,
                            "com.sandlotgames.snailmail." + name], check=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
