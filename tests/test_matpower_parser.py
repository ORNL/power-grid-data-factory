"""Golden and format tests for the shared MATPOWER parser."""
from __future__ import annotations

import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from _bootstrap import REPO_ROOT, case_available

from grid_data_factory.parsers.matpower import parse_matpower_case, parse_matrix, write_matpower_case
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

    def test_control_fields_survive_round_trip(self):
        case = {
            "case_id": "controls",
            "base_mva": 100.0,
            "buses": [
                {"bus_id": "1", "type": 3, "vm": 1.02, "va": 0.0, "vmin": 0.9, "vmax": 1.1},
                {"bus_id": "2", "type": 1, "vm": 1.0, "va": 0.0, "vmin": 0.9, "vmax": 1.1},
            ],
            "loads": [{"load_id": "load_1", "bus_id": "2", "pd": 40.0, "qd": 10.0}],
            "generators": [{
                "gen_id": "gen_000001", "bus_id": "1", "pg": 42.5, "qg": 11.5,
                "vg": 1.02, "mbase": 100.0, "status": 1, "pmin": 0.0,
                "pmax": 100.0, "qmin": -50.0, "qmax": 50.0,
            }],
            "branches": [{
                "branch_id": "branch_000001", "from": "1", "to": "2", "r": 0.01,
                "x": 0.1, "b": 0.02, "rate_a": 80.0, "rate_b": 90.0,
                "rate_c": 100.0, "tap": 1.05, "shift": 3.0, "status": 1,
                "angmin": -30.0, "angmax": 30.0,
            }],
        }
        with TemporaryDirectory() as tmp:
            parsed = parse_matpower_case(write_matpower_case(case, Path(tmp) / "case.m"), "controls")
        self.assertEqual(parsed["generators"][0]["pg"], 42.5)
        self.assertEqual(parsed["generators"][0]["qg"], 11.5)
        self.assertEqual(parsed["generators"][0]["vg"], 1.02)
        self.assertEqual(parsed["branches"][0]["tap"], 1.05)
        self.assertEqual(parsed["branches"][0]["shift"], 3.0)


if __name__ == "__main__":
    unittest.main()
