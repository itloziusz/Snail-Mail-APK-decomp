"""End-to-end checks for the deliberately small A32 translation contract."""

from pathlib import Path
import shutil
import struct
import subprocess
import sys
import tempfile
import unittest

from recompile import Unsupported, translate


def a32(*words: int) -> bytes:
    return struct.pack("<" + "I" * len(words), *words)


class RecompilerTests(unittest.TestCase):
    def compile_and_run(self, code: bytes, args: list[str], limit: int = 100):
        if not shutil.which("cc"):
            self.skipTest("a C compiler is unavailable")
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "guest.c"
            program = Path(directory) / "guest"
            source.write_text(translate(code, 0x1000, limit))
            subprocess.run(["cc", "-std=c99", "-Wall", "-Wextra", "-Werror",
                            str(source), "-o", str(program)], check=True)
            return subprocess.run([str(program), *args], capture_output=True, text=True)

    def test_counting_loop_and_conditions(self):
        # MOV r0,#0; ADD r0,r0,#1; CMP r0,r1; BNE -4; BX LR
        code = a32(0xE3A00000, 0xE2800001, 0xE1500001,
                   0x1AFFFFFC, 0xE12FFF1E)
        result = self.compile_and_run(code, ["9", "5"])
        self.assertEqual((result.returncode, result.stdout), (0, "5\n"))

    def test_32_bit_wrap_and_argument_validation(self):
        code = a32(0xE2800001, 0xE12FFF1E)
        self.assertEqual(self.compile_and_run(code, ["4294967295"]).stdout, "0\n")
        self.assertEqual(self.compile_and_run(code, ["4294967296"]).returncode, 2)

    def test_step_budget(self):
        result = self.compile_and_run(a32(0xEAFFFFFE), [], limit=3)
        self.assertEqual(result.returncode, 1)
        self.assertIn("step limit", result.stderr)

    def test_rejects_unsupported_and_out_of_range_code(self):
        for code in (a32(0xE5910000), a32(0xEB000000), a32(0xEA000000), b"\x00"):
            with self.subTest(code=code.hex()), self.assertRaises(Unsupported):
                translate(code, 0x1000, 100)

    def test_cli_does_not_write_output_on_failure(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "bad.bin"
            output = Path(directory) / "bad.c"
            source.write_bytes(a32(0xE5910000))
            result = subprocess.run([sys.executable, str(Path(__file__).with_name("recompile.py")),
                                     str(source), "--output", str(output)],
                                    capture_output=True, text=True)
            self.assertEqual(result.returncode, 2)
            self.assertFalse(output.exists())


if __name__ == "__main__":
    unittest.main()
