"""Tests for the pandapower solver adapter."""
from __future__ import annotations

import unittest
from pathlib import Path

from _bootstrap import REPO_ROOT  # noqa: F401  (ensures src on path)

from grid_data_factory.solvers import pandapower_adapter


class ParsePpExceptionTests(unittest.TestCase):
    def test_nonconverged(self):
        self.assertEqual(pandapower_adapter._parse_pp_exception(RuntimeError("OPF did not converge")), "nonconverged")

    def test_process_error(self):
        self.assertEqual(pandapower_adapter._parse_pp_exception(ValueError("boom")), "process_error:ValueError")


class RunPandapowerCaseTests(unittest.TestCase):
    def test_result_shape_is_stable(self):
        result = pandapower_adapter.run_pandapower_case(Path("/nonexistent/case.m"))
        self.assertEqual(result["solver_name"], "pandapower")
        self.assertFalse(result["success"])
        self.assertIn("wallclock_seconds", result["runtime_metadata"])


if __name__ == "__main__":
    unittest.main()
