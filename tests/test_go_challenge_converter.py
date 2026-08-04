"""Tests for the GO Challenge PSS/E RAW/ROP -> MATPOWER converter."""
from __future__ import annotations

import unittest
from pathlib import Path

from _bootstrap import REPO_ROOT  # noqa: F401  (ensures src on path)

from grid_data_factory.parsers import go_challenge as gc


_RAW = """IC, 100.00, 33, 0, 1
comment line 1
comment line 2
1, 'BUS1', 138.0, 3, 1, 1, 1, 1.05, 0.0, 1.1, 0.9, 1, 1
2, 'BUS2', 138.0, 1, 1, 1, 1, 1.0, 0.0, 1.1, 0.9, 1, 1
0 / END OF BUS DATA
1, '1', 1, 1, 1, 20.0, 10.0
0 / END OF LOAD DATA
0 / END OF FIXED SHUNT DATA
1, '1', 10.0, 0.0, 50.0, -50.0, 1.0, 0, 100.0, 1, 0, 0, 0, 0, 1, 100, 200.0, 10.0
0 / END OF GENERATOR DATA
1, 2, '1', 0.01, 0.1, 0.02, 100.0, 100.0, 100.0, 0, 0, 1, 0, 1
0 / END OF BRANCH DATA
0 / END OF TRANSFORMER DATA
"""

_ROP = """BEGIN GENERATOR DISPATCH DATA
1, '1', 1.0, 5
0 / END GENERATOR DISPATCH DATA BEGIN ACTIVE POWER DISPATCH TABLE DATA
5, 200.0, 10.0, 1.0, 0, 0, 7
0 / END ACTIVE POWER DISPATCH TABLE DATA
BEGIN PIECE-WISE LINEAR COST TABLES
7, 'label', 2
0.0, 0.0
200.0, 4000.0
0 / END PIECE-WISE LINEAR COST TABLES
"""


class SmallHelperTests(unittest.TestCase):
    def test_safe_case_symbol(self):
        self.assertEqual(gc.safe_case_symbol("go 1/case.raw"), "go_1_case_raw")
        self.assertEqual(gc.safe_case_symbol("123abc"), "case_123abc")
        self.assertEqual(gc.safe_case_symbol("///"), "case_go")

    def test_resolve_network_root(self):
        self.assertEqual(gc.resolve_network_root("net1/scenario_01/case.raw"), Path("net1"))
        self.assertEqual(gc.resolve_network_root("net1/case.raw"), Path("net1"))

    def test_candidate_raw_entries_from_names(self):
        names = ["a/case.RAW", "b/", "c/notes.txt", "d/other.raw"]
        self.assertEqual(gc.candidate_raw_entries_from_names(names), ["a/case.RAW", "d/other.raw"])


class ParseRawTests(unittest.TestCase):
    def test_parse_counts(self):
        parsed = gc.parse_raw_text(_RAW)
        self.assertEqual(parsed.base_mva, 100.0)
        self.assertEqual(len(parsed.bus_rows), 2)
        self.assertEqual(len(parsed.load_rows), 1)
        self.assertEqual(len(parsed.gen_rows), 1)
        self.assertEqual(len(parsed.branch_rows), 1)
        self.assertEqual(len(parsed.transformer_rows), 0)

    def test_too_short_raises(self):
        with self.assertRaises(ValueError):
            gc.parse_raw_text("one\ntwo\n")


class ParseRopTests(unittest.TestCase):
    def test_maps(self):
        rop = gc.parse_rop_text(_ROP)
        self.assertEqual(rop.gen_dispatch[(1, "1")], 5)
        self.assertEqual(rop.active_dispatch[5], (200.0, 10.0, 7))
        self.assertEqual(rop.pwl_tables[7], [(0.0, 0.0), (200.0, 4000.0)])


class RowsToMatpowerTests(unittest.TestCase):
    def test_end_to_end_with_rop(self):
        parsed = gc.parse_raw_text(_RAW)
        rop = gc.parse_rop_text(_ROP)
        text, stats = gc.rows_to_matpower_text("go_demo", parsed, rop, use_rop_limits=True)

        self.assertEqual(stats["buses"], 2)
        self.assertEqual(stats["generators"], 1)
        self.assertEqual(stats["branches"], 1)
        self.assertIn("function mpc = go_demo", text)
        self.assertIn("mpc.baseMVA = 100;", text)
        for field in ("bus", "gen", "branch", "gencost"):
            self.assertIn(f"mpc.{field} = [", text)
        # Piecewise (0,0)->(200,4000) linearizes to slope 20 in the gencost row.
        gencost_line = [ln for ln in text.splitlines() if ln.strip().endswith(";") and "\t20\t" in ln]
        self.assertTrue(gencost_line, "expected linearized cost slope 20 in gencost matrix")

    def test_default_linear_cost_without_rop(self):
        parsed = gc.parse_raw_text(_RAW)
        _, stats = gc.rows_to_matpower_text("go_demo", parsed, None, use_rop_limits=False)
        self.assertEqual(stats["generators"], 1)


if __name__ == "__main__":
    unittest.main()
