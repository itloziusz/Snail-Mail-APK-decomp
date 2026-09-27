"""Integration checks for extraction, inspection, and compiled-vector helpers."""

from pathlib import Path
import shutil
import struct
import subprocess
import sys
import tempfile
import unittest

from extract_elf import ExtractionError, extract, list_functions
from analyze import inspect
from verify import load_cases, verify


WORDS = (0xE3A00000, 0xE2800001, 0xE1500001, 0x1AFFFFFC, 0xE12FFF1E)
CODE = struct.pack("<5I", *WORDS)


def make_elf(code=CODE, symbol_address=0x1000):
    """Build a tiny ELF32 ARM shared-object fixture without a cross compiler."""
    shstr = b"\x00.text\x00.symtab\x00.strtab\x00.shstrtab\x00"
    names = {name: shstr.index(name.encode()) for name in
             (".text", ".symtab", ".strtab", ".shstrtab")}
    ident = b"\x7fELF\x01\x01\x01" + b"\x00" * 9
    data = bytearray(ident + struct.pack("<HHIIIIIHHHHHH",
                                         3, 40, 1, 0, 0, 0, 0, 52, 0, 0, 40, 5, 4))

    def add(payload):
        while len(data) % 4:
            data.append(0)
        offset = len(data)
        data.extend(payload)
        return offset

    text_off = add(code)
    symbol = struct.pack("<IIIBBH", 1, symbol_address, len(code), 0x12, 0, 1)
    sym_off = add(b"\x00" * 16 + symbol)
    strtab = b"\x00foo\x00"
    str_off = add(strtab)
    shstr_off = add(shstr)
    while len(data) % 4:
        data.append(0)
    shoff = len(data)
    sh = lambda *fields: struct.pack("<IIIIIIIIII", *fields)
    data.extend(b"\x00" * 40)
    data.extend(sh(names[".text"], 1, 6, 0x1000, text_off, len(code), 0, 0, 4, 0))
    data.extend(sh(names[".symtab"], 2, 0, 0, sym_off, 32, 3, 1, 4, 16))
    data.extend(sh(names[".strtab"], 3, 0, 0, str_off, len(strtab), 0, 0, 1, 0))
    data.extend(sh(names[".shstrtab"], 3, 0, 0, shstr_off, len(shstr), 0, 0, 1, 0))
    struct.pack_into("<I", data, 32, shoff)
    return bytes(data)


class HelperTests(unittest.TestCase):
    def test_extract_inspect_and_verify_pipeline(self):
        if not shutil.which("cc"):
            self.skipTest("C compiler unavailable")
        with tempfile.TemporaryDirectory() as directory:
            elf = Path(directory) / "sample.so"
            elf.write_bytes(make_elf())
            self.assertEqual(list_functions(elf)[0]["name"], "foo")
            code, provenance = extract(elf, "foo")
            self.assertEqual(code, CODE)
            self.assertEqual(provenance["base"], "0x00001000")
            report = inspect(code, 0x1000)
            self.assertTrue(report["ready_for_starter"])
            self.assertEqual(report["supported"], 5)
            cases = load_cases(Path(__file__).parent / "examples/count_cases.json")
            self.assertEqual(verify(code, cases, 0x1000, "cc", 100, 5), [])

    def test_inspection_records_unsupported_without_guessing(self):
        report = inspect(CODE + struct.pack("<I", 0xE5910000), 0x1000, 1)
        self.assertEqual(report["unsupported"], 1)
        self.assertFalse(report["ready_for_starter"])
        self.assertEqual(report["issue_examples"][0]["address"], "0x00001014")

    def test_elf_rejects_thumb_and_missing_symbol(self):
        with tempfile.TemporaryDirectory() as directory:
            elf = Path(directory) / "sample.so"
            elf.write_bytes(make_elf(symbol_address=0x1001))
            with self.assertRaisesRegex(ExtractionError, "Thumb"):
                extract(elf, "foo")
            with self.assertRaisesRegex(ExtractionError, "unambiguous"):
                extract(elf, "bar")

    def test_malformed_elf_reports_error(self):
        folder = Path(__file__).parent
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "broken.so"
            path.write_bytes(b"not an ELF")
            result = subprocess.run([sys.executable, str(folder / "extract_elf.py"),
                                     str(path), "--list"], capture_output=True, text=True)
            self.assertEqual(result.returncode, 2)
            self.assertIn("extract_elf:", result.stderr)

    def test_vectors_reject_invalid_values_and_report_mismatch(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "cases.json"
            path.write_text('{"cases":[{"args":[true],"expect":1}]}')
            with self.assertRaisesRegex(ValueError, "uint32"):
                load_cases(path)
        if shutil.which("cc"):
            mismatch = verify(CODE, [{"args": [0, 5], "expect": 6}],
                              0x1000, "cc", 100, 5)
            self.assertEqual(len(mismatch), 1)
            self.assertIn("expected 6", mismatch[0])

    def test_command_line_pipeline_and_strict_failure(self):
        if not shutil.which("cc"):
            self.skipTest("C compiler unavailable")
        folder = Path(__file__).parent
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            elf = root / "fixture.so"
            raw = root / "foo.a32"
            manifest = root / "foo.json"
            report = root / "coverage.json"
            elf.write_bytes(make_elf())

            def run(script, *args):
                return subprocess.run([sys.executable, str(folder / script), *map(str, args)],
                                      capture_output=True, text=True)

            result = run("extract_elf.py", elf, "--symbol", "foo", "--output", raw,
                         "--manifest", manifest)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(raw.read_bytes(), CODE)
            self.assertIn('"source_sha256"', manifest.read_text())
            result = run("analyze.py", raw, "--base", "0x1000", "--strict", "--output", report)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn('"ready_for_starter": true', report.read_text())
            result = run("verify.py", raw, folder / "examples/count_cases.json", "--base", "0x1000")
            self.assertEqual((result.returncode, result.stdout), (0, "PASS: 3 cases\n"))
            raw.write_bytes(CODE + struct.pack("<I", 0xE5910000))
            result = run("analyze.py", raw, "--strict", "--output", report)
            self.assertEqual(result.returncode, 2)
            self.assertIn('"unsupported": 1', report.read_text())


if __name__ == "__main__":
    unittest.main()
