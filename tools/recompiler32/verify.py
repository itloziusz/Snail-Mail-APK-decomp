#!/usr/bin/env python3
"""Compile the starter's generated C and compare outputs with known vectors."""

import argparse
import json
from pathlib import Path
import shutil
import subprocess
import tempfile

from recompile import Unsupported, translate


def load_cases(path: Path) -> list[dict]:
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict) or set(data) != {"cases"} or not isinstance(data["cases"], list):
        raise ValueError("case file must be an object with a cases array")
    if not 1 <= len(data["cases"]) <= 1000:
        raise ValueError("case file must contain 1..1000 cases")
    for i, case in enumerate(data["cases"]):
        if not isinstance(case, dict) or set(case) != {"args", "expect"}:
            raise ValueError(f"case {i}: expected only args and expect")
        args = case["args"]
        if not isinstance(args, list) or len(args) > 4:
            raise ValueError(f"case {i}: args must be a list of up to four uint32 values")
        for value in [*args, case["expect"]]:
            if type(value) is not int or not 0 <= value <= 0xFFFFFFFF:
                raise ValueError(f"case {i}: values must be uint32 integers")
    return data["cases"]


def verify(data: bytes, cases: list[dict], base: int, cc: str, max_steps: int,
           timeout: float) -> list[str]:
    if not 0 < timeout <= 60:
        raise ValueError("timeout must be greater than 0 and at most 60 seconds")
    compiler = shutil.which(cc)
    if compiler is None:
        raise ValueError(f"compiler not found: {cc}")
    source = translate(data, base, max_steps)
    failures = []
    with tempfile.TemporaryDirectory(prefix="a32-verify-") as directory:
        source_path = Path(directory) / "guest.c"
        exe = Path(directory) / "guest"
        source_path.write_text(source, encoding="utf-8")
        compile_result = subprocess.run(
            [compiler, "-std=c99", "-Wall", "-Wextra", "-Werror", "-O2",
             str(source_path), "-o", str(exe)], capture_output=True, text=True,
            timeout=timeout)
        if compile_result.returncode:
            return ["C compilation failed: " + compile_result.stderr[:2000]]
        for i, case in enumerate(cases):
            try:
                result = subprocess.run([str(exe), *(str(a) for a in case["args"])],
                                        capture_output=True, text=True, timeout=timeout)
            except subprocess.TimeoutExpired:
                failures.append(f"case {i}: timed out")
                continue
            expected = f"{case['expect']}\n"
            if result.returncode or result.stdout != expected:
                failures.append(f"case {i}: expected {expected.strip()}, got "
                                f"exit={result.returncode}, stdout={result.stdout!r}, "
                                f"stderr={result.stderr[:200]!r}")
    return failures


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("input", type=Path, help="raw little-endian A32 code")
    parser.add_argument("cases", type=Path, help="JSON with args and expected r0")
    parser.add_argument("--base", type=lambda value: int(value, 0), default=0x1000)
    parser.add_argument("--cc", default="cc")
    parser.add_argument("--max-steps", type=int, default=1000000)
    parser.add_argument("--timeout", type=float, default=5)
    args = parser.parse_args()
    try:
        cases = load_cases(args.cases)
        failures = verify(args.input.read_bytes(), cases, args.base, args.cc,
                          args.max_steps, args.timeout)
    except (OSError, ValueError, Unsupported, json.JSONDecodeError,
            subprocess.TimeoutExpired) as exc:
        parser.exit(2, f"verify: {exc}\n")
    if failures:
        for failure in failures:
            print(f"FAIL: {failure}")
        return 1
    print(f"PASS: {len(cases)} cases")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
