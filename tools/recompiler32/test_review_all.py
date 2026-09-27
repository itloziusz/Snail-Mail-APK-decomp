"""Check that the census does not silently invent function names or tests."""

import json
from pathlib import Path
import tempfile
import unittest

from review_all import matching_names, reference_suites


class ReviewTests(unittest.TestCase):
    def test_aot_name_must_be_indexed_alias(self):
        row = {"name": "restore_core_regs", "same_address_symbols": ["__restore_core_regs"]}
        self.assertTrue(matching_names(row, {"name": "restore_core_regs"}))
        self.assertTrue(matching_names(row, {"name": "__restore_core_regs"}))
        self.assertFalse(matching_names(row, {"name": "another_function"}))

    def test_only_exact_zero_mismatch_addresses_count(self):
        data = {"reference": {"sha256": "abc"}, "suites": [
            {"name": "exact", "mismatches": 0, "original_functions": ["v7a:0x1234"]},
            {"name": "range", "mismatches": 0, "original_functions": ["v7a:0x2000-0x3000"]},
            {"name": "failing", "mismatches": 1, "original_functions": ["v7a:0x4000"]}]}
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "result.json"
            path.write_text(json.dumps(data))
            self.assertEqual(reference_suites(path, "abc"), {0x1234: ["exact"]})
            with self.assertRaisesRegex(ValueError, "hash"):
                reference_suites(path, "wrong")


if __name__ == "__main__":
    unittest.main()
