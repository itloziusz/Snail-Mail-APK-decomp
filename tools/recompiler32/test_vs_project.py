"""Validate the exported Visual Studio project without requiring Windows."""

import json
from pathlib import Path
import shutil
import struct
import subprocess
import sys
import tempfile
import unittest
import xml.etree.ElementTree as ET

from recompile import translate
from vs_project import export_project


CODE = struct.pack("<5I", 0xE3A00000, 0xE2800001, 0xE1500001,
                   0x1AFFFFFC, 0xE12FFF1E)


class VisualStudioExportTests(unittest.TestCase):
    def test_project_is_editable_x64_c_and_preserves_input(self):
        with tempfile.TemporaryDirectory() as directory:
            project = Path(directory) / "CountProject"
            source = translate(CODE, 0x1000, 100)
            export_project(CODE, source, 0x1000, 100, project, "CountProject")
            self.assertEqual((project / "source.a32").read_bytes(), CODE)
            self.assertEqual((project / "CountProject.c").read_text(), source)
            manifest = json.loads((project / "manifest.json").read_text())
            self.assertEqual((manifest["base"], manifest["max_steps"]), ("0x00001000", 100))
            self.assertIn("# Visual Studio Version 17", (project / "CountProject.sln").read_text(encoding="utf-8-sig"))
            tree = ET.parse(project / "CountProject.vcxproj")
            ns = {"m": "http://schemas.microsoft.com/developer/msbuild/2003"}
            configs = [x.attrib["Include"] for x in tree.findall(".//m:ProjectConfiguration", ns)]
            self.assertEqual(configs, ["Debug|x64", "Release|x64"])
            self.assertEqual([x.text for x in tree.findall(".//m:PlatformToolset", ns)], ["v143", "v143"])
            self.assertEqual([x.text for x in tree.findall(".//m:CompileAs", ns)], ["CompileAsC"] * 2)
            self.assertTrue(all("/std:c11" in x.text for x in tree.findall(".//m:AdditionalOptions", ns)))
            self.assertEqual([x.attrib["Include"] for x in tree.findall(".//m:ClCompile", ns)
                              if "Include" in x.attrib], ["CountProject.c"])
            if shutil.which("cc"):
                exe = Path(directory) / "host_check"
                subprocess.run(["cc", "-std=c11", "-Wall", "-Wextra", "-Werror",
                                str(project / "CountProject.c"), "-o", str(exe)], check=True)
                result = subprocess.run([str(exe), "0", "5"], capture_output=True, text=True)
                self.assertEqual((result.returncode, result.stdout), (0, "5\n"))

    def test_cli_refuses_to_overwrite_hand_edits(self):
        folder = Path(__file__).parent
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "count.a32"
            target = Path(directory) / "Editable"
            source.write_bytes(CODE)
            command = [sys.executable, str(folder / "recompile.py"), str(source),
                       "--vs-project", str(target), "--project-name", "Editable"]
            first = subprocess.run(command, capture_output=True, text=True)
            self.assertEqual(first.returncode, 0, first.stderr)
            c_path = target / "Editable.c"
            c_path.write_text(c_path.read_text() + "/* handwritten change */\n")
            second = subprocess.run(command, capture_output=True, text=True)
            self.assertEqual(second.returncode, 2)
            self.assertIn("already exists", second.stderr)
            self.assertTrue(c_path.read_text().endswith("/* handwritten change */\n"))

    def test_invalid_name_and_unsupported_input_leave_no_project(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            with self.assertRaisesRegex(ValueError, "project name"):
                export_project(CODE, translate(CODE, 0x1000, 100), 0x1000,
                               100, root / "Bad", "../Bad")
            self.assertFalse((root / "Bad").exists())
            invalid = root / "invalid.a32"
            invalid.write_bytes(struct.pack("<I", 0xE5910000))
            result = subprocess.run([sys.executable, str(Path(__file__).with_name("recompile.py")),
                                     str(invalid), "--vs-project", str(root / "NoProject")],
                                    capture_output=True, text=True)
            self.assertEqual(result.returncode, 2)
            self.assertFalse((root / "NoProject").exists())


if __name__ == "__main__":
    unittest.main()
