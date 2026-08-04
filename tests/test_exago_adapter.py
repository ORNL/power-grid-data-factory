"""Tests for the ExaGO solver adapter (path resolution and stdout parsing)."""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from _bootstrap import REPO_ROOT  # noqa: F401  (ensures src on path)

from grid_data_factory.solvers import exago_adapter


class ResolveOpflowBinTests(unittest.TestCase):
    def setUp(self):
        self.repo = Path("/tmp/repo").resolve()
        self.exago = self.repo / "external" / "ExaGO"

    def test_explicit_relative_bin_joins_repo_root(self):
        got = exago_adapter.resolve_opflow_bin(self.repo, self.exago, opflow_bin="bin/opflow")
        self.assertEqual(got, (self.repo / "bin" / "opflow").resolve())

    def test_explicit_absolute_bin_is_returned(self):
        got = exago_adapter.resolve_opflow_bin(self.repo, self.exago, opflow_bin="/opt/opflow")
        self.assertEqual(got, Path("/opt/opflow"))

    def test_install_prefix(self):
        got = exago_adapter.resolve_opflow_bin(self.repo, self.exago, exago_install="build/inst")
        self.assertEqual(got, (self.repo / "build" / "inst").resolve() / "bin" / "opflow")

    def test_build_profile(self):
        got = exago_adapter.resolve_opflow_bin(self.repo, self.exago, build_profile="frontier")
        self.assertEqual(got, (self.exago / "builds" / "frontier" / "install" / "bin" / "opflow").resolve())

    def test_default(self):
        got = exago_adapter.resolve_opflow_bin(self.repo, self.exago)
        self.assertEqual(got, (self.exago / "install" / "bin" / "opflow").resolve())


class ParseStdoutSolutionTests(unittest.TestCase):
    STDOUT = (
        "Bus        Pd\n"
        "1   0.0  0.0  0.0  0.0  1.05  0.0\n"
        "\n"
        "Gen      Status     Fuel\n"
        "1  1  COAL  50.0  10.0  0  0  0  0\n"
    )

    def test_parses_bus_and_gen(self):
        sol = exago_adapter._parse_exago_solution(self.STDOUT, {"base_mva": 100.0})
        self.assertEqual(sol["baseMVA"], 100.0)
        self.assertEqual(sol["bus"]["1"], {"vm": 1.05, "va": 0.0})
        self.assertEqual(sol["gen"]["1"], {"pg": 0.5, "qg": 0.1, "pg_cost": 0.0})
        self.assertEqual(sol["branch"], {})
        self.assertTrue(sol["per_unit"])

    def test_json_export_missing_returns_none(self):
        with tempfile.TemporaryDirectory() as tmp:
            missing = Path(tmp) / "nope.json"
            self.assertIsNone(exago_adapter._parse_exago_json_export(missing, {"base_mva": 100.0}))


if __name__ == "__main__":
    unittest.main()
