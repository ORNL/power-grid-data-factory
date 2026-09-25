import subprocess
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from _bootstrap import REPO_ROOT  # noqa: F401  (ensures src on path)

from grid_data_factory.solvers.powermodels_adapter import PowerModelsAdapter


class PowerModelsScopfTests(unittest.TestCase):
    def test_solve_scopf_routes_to_pmsc_runner(self) -> None:
        with TemporaryDirectory() as tmp:
            adapter = PowerModelsAdapter(repo_root=Path(tmp))
            contingencies = [
                {
                    "contingency_id": "line_out",
                    "event_type": "simultaneous",
                    "components": [{"type": "branch", "id": "branch_000001"}],
                }
            ]
            with patch.object(adapter, "_run_julia_script", return_value={"success": True}) as run:
                result = adapter.solve_scopf({"case_id": "case"}, contingencies, {"tol": 1e-7})

            self.assertTrue(result["success"])
            run.assert_called_once_with(
                "run_batch.jl",
                {"case_id": "case"},
                {"task": "scopf", "contingencies": contingencies, "options": {"tol": 1e-7}},
            )

    def test_scopf_preflight_imports_pmsc(self) -> None:
        with TemporaryDirectory() as tmp:
            adapter = PowerModelsAdapter(repo_root=Path(tmp))
            completed = subprocess.CompletedProcess([], 0, stdout="PREFLIGHT_OK\n", stderr="")
            with patch("grid_data_factory.solvers.powermodels_adapter.subprocess.run", return_value=completed) as run:
                result = adapter._run_preflight({}, 120.0, task="scopf")

            self.assertIsNone(result)
            command = run.call_args.args[0][-1]
            self.assertIn("PowerModelsSecurityConstrained", command)

    def test_ac_opf_preflight_does_not_require_pmsc(self) -> None:
        with TemporaryDirectory() as tmp:
            adapter = PowerModelsAdapter(repo_root=Path(tmp))
            completed = subprocess.CompletedProcess([], 0, stdout="PREFLIGHT_OK\n", stderr="")
            with patch("grid_data_factory.solvers.powermodels_adapter.subprocess.run", return_value=completed) as run:
                result = adapter._run_preflight({}, 120.0, task="ac_opf")

            self.assertIsNone(result)
            command = run.call_args.args[0][-1]
            self.assertNotIn("PowerModelsSecurityConstrained", command)


if __name__ == "__main__":
    unittest.main()
