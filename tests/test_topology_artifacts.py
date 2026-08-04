"""Tests for topology-artifact construction helpers."""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from _bootstrap import REPO_ROOT  # noqa: F401  (ensures src on path)

from grid_data_factory.topology import artifacts


_MINI_CASE = """function mpc = mini
mpc.baseMVA = 100;
mpc.bus = [
	1	3	0	0	0	0	1	1.06	0	230	1	1.06	0.94;
	2	1	21.7	12.7	0	0	1	1.045	0	230	1	1.06	0.94;
];
mpc.gen = [
	1	0	0	10	-10	1.06	100	1	40	0	0	0	0	0	0	0	0	0	0	0	0;
];
mpc.branch = [
	1	2	0.01938	0.05917	0.0528	0	0	0	0	0	1	-360	360;
];
mpc.gencost = [
	2	0	0	3	0.01	0.3	0.2;
];
"""


class ParseTopologyCaseTests(unittest.TestCase):
    def _write(self, tmp: Path) -> Path:
        case = tmp / "mini.m"
        case.write_text(_MINI_CASE, encoding="utf-8")
        return case

    def test_parse_shapes(self):
        with tempfile.TemporaryDirectory() as t:
            parsed = artifacts.parse_topology_case(self._write(Path(t)))
        self.assertEqual(parsed["base_mva"], 100.0)
        self.assertEqual(len(parsed["buses"]), 2)
        self.assertEqual(len(parsed["branches"]), 1)
        self.assertEqual(len(parsed["generators"]), 1)
        # Only bus 2 has load.
        self.assertEqual(len(parsed["loads"]), 1)
        self.assertEqual(parsed["loads"][0]["nominal_pd"], 21.7)
        self.assertEqual(parsed["generators"][0]["cost"], [0.01, 0.3, 0.2])

    def test_missing_sections_raise(self):
        with tempfile.TemporaryDirectory() as t:
            bad = Path(t) / "bad.m"
            bad.write_text("mpc.baseMVA = 100;\n", encoding="utf-8")
            with self.assertRaises(ValueError):
                artifacts.parse_topology_case(bad)


class NextTopologyIndexTests(unittest.TestCase):
    def test_empty_and_increment(self):
        with tempfile.TemporaryDirectory() as t:
            case_dir = Path(t) / "case"
            self.assertEqual(artifacts.next_topology_index(case_dir), 0)
            case_dir.mkdir()
            (case_dir / "topology_000000_baseline.json").write_text("{}", encoding="utf-8")
            (case_dir / "topology_000003_variant.json").write_text("{}", encoding="utf-8")
            self.assertEqual(artifacts.next_topology_index(case_dir), 4)


class ResolveSourceCaseFileTests(unittest.TestCase):
    def test_explicit_relative(self):
        repo = Path("/tmp/repo").resolve()
        out = artifacts.resolve_source_case_file(repo, "pglib", "case14", "cases/x.m")
        self.assertEqual(out, (repo / "cases/x.m").resolve())

    def test_pglib_default(self):
        repo = Path("/tmp/repo").resolve()
        out = artifacts.resolve_source_case_file(repo, "pglib", "case14", None)
        self.assertEqual(out, (repo / "external" / "pglib-opf" / "case14.m").resolve())

    def test_unknown_source_raises(self):
        with self.assertRaises(ValueError):
            artifacts.resolve_source_case_file(Path("/tmp"), "mystery", "c", None)


class BuildTopologyArtifactTests(unittest.TestCase):
    def test_fields_and_counts(self):
        parsed = {
            "base_mva": 100.0,
            "buses": [{"bus_id": "1"}, {"bus_id": "2"}],
            "branches": [{"branch_id": "branch_000001"}],
            "generators": [{"gen_id": "gen_000001"}],
            "loads": [],
        }
        art = artifacts.build_topology_artifact(
            "topology_000000_baseline", "case14", "pglib", "baseline", Path("/tmp/case14.m"), parsed,
        )
        self.assertEqual(art["topology_id"], "topology_000000_baseline")
        self.assertEqual(art["counts"], {"buses": 2, "branches": 1, "generators": 1, "loads": 0})
        self.assertTrue(art["created_at"])  # ISO timestamp present


if __name__ == "__main__":
    unittest.main()
