"""Golden and format tests for the shared MATPOWER parser."""
from __future__ import annotations

import unittest

from _bootstrap import REPO_ROOT, case_available

from grid_data_factory.parsers.matpower import parse_matpower_case, parse_matrix
from grid_data_factory.sources.registry import resolve_case_file


class TestMatpowerParser(unittest.TestCase):
    def test_semicolon_golden_case14(self):
        cid = "pglib_opf_case14_ieee"
        if not case_available(cid):
            self.skipTest(f"{cid} not available")
        d = parse_matpower_case(resolve_case_file(REPO_ROOT, cid), cid)
        self.assertEqual(len(d["buses"]), 14)
        self.assertEqual(len(d["generators"]), 5)
        self.assertEqual(len(d["branches"]), 20)
        self.assertEqual(len(d["loads"]), 11)

    def test_newline_golden_new_england(self):
        cid = "epigrids_new_england_250"
        if not case_available(cid):
            self.skipTest(f"{cid} not available")
        d = parse_matpower_case(resolve_case_file(REPO_ROOT, cid), cid)
        self.assertEqual(len(d["buses"]), 250)
        self.assertEqual(len(d["generators"]), 42)
        self.assertEqual(len(d["branches"]), 339)
        self.assertEqual(len(d["loads"]), 168)

    def test_parse_matrix_semicolon_rows(self):
        content = "mpc.bus = [\n1 1 0 0 0 0 1 1 0 0 1 1.1 0.9;\n2 2 0 0 0 0 1 1 0 0 1 1.1 0.9;\n];"
        rows = parse_matrix(content, "bus")
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[0][0], 1)

    def test_parse_matrix_newline_rows(self):
        cols = " ".join(["1"] + ["0"] * 12)
        cols2 = " ".join(["2"] + ["0"] * 12)
        content = f"mpc.bus = [\n{cols}\n{cols2}\n];"
        rows = parse_matrix(content, "bus")
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[1][0], 2)

    def test_missing_section_raises(self):
        content = "mpc.baseMVA = 100;\nmpc.bus = [\n1 1 0 0 0 0 1 1 0 0 1 1.1 0.9;\n];"
        cf = REPO_ROOT / "tests" / "_tmp_missing.m"
        cf.write_text(content, encoding="utf-8")
        try:
            with self.assertRaises(ValueError):
                parse_matpower_case(cf, "tmp")
        finally:
            cf.unlink()


if __name__ == "__main__":
    unittest.main()
