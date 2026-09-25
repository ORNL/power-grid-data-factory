from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest.mock import patch

from _bootstrap import REPO_ROOT  # noqa: F401  (ensures src on path)

sys.path.insert(0, str(REPO_ROOT / "scripts"))
import run_scopf  # noqa: E402


class ContingencySetLoadingTests(unittest.TestCase):
    def test_loads_case_filtered_campaign_jsonl(self) -> None:
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / "screened.jsonl"
            rows = [
                {
                    "case_id": "case_a",
                    "contingency": {
                        "contingency_id": "line_a",
                        "event_type": "simultaneous",
                        "components": [{"type": "branch", "id": "branch_000001"}],
                    },
                },
                {
                    "case_id": "case_b",
                    "contingency": {
                        "contingency_id": "line_b",
                        "event_type": "simultaneous",
                        "components": [{"type": "branch", "id": "branch_000002"}],
                    },
                },
            ]
            path.write_text("\n".join(json.dumps(row) for row in rows) + "\n", encoding="utf-8")

            set_id, contingencies = run_scopf.load_contingency_set(path, "case_a")

            self.assertEqual(set_id, "screened")
            self.assertEqual([item["contingency_id"] for item in contingencies], ["line_a"])
            self.assertEqual(contingencies[0]["order"], 1)

    def test_rejects_simultaneous_n2(self) -> None:
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / "n2.json"
            path.write_text(
                json.dumps(
                    {
                        "contingencies": [
                            {
                                "event_type": "simultaneous",
                                "components": [
                                    {"type": "branch", "id": "branch_000001"},
                                    {"type": "branch", "id": "branch_000002"},
                                ],
                            }
                        ]
                    }
                ),
                encoding="utf-8",
            )

            with self.assertRaisesRegex(ValueError, "not a static N-1 event"):
                run_scopf.load_contingency_set(path, "case_a")


class _FakeAdapter:
    def __init__(self) -> None:
        self.calls = []

    def solve_scopf(self, case, contingencies, options=None):
        self.calls.append((case, contingencies, options))
        return {
            "success": True,
            "termination_status": "LOCALLY_SOLVED",
            "solver_name": "powermodels_security_constrained",
            "objective": 12.5,
            "solve_time": 0.25,
            "contingency_count": len(contingencies),
            "raw_result": {"solution": {}},
            "runtime_metadata": {
                "wallclock_seconds": 0.5,
                "execution_context": {"mpi_processes": 1, "gpu_enabled": False, "gpu_type": None},
            },
        }


class ScopfAttemptTests(unittest.TestCase):
    def test_run_one_case_finalizes_preserved_attempt(self) -> None:
        with TemporaryDirectory() as tmp:
            repo_root = Path(tmp)
            case_file = repo_root / "case.m"
            case_file.write_text("function mpc = case\n", encoding="utf-8")
            args = SimpleNamespace(
                runs_root="runs",
                topology_id="topology_base",
                operating_point_id="op_base",
                solver_id="pmsc_ipopt",
                source="pglib",
                contingency_set="set.json",
                timeout_s=30.0,
                tol=1e-7,
                max_iter=100,
            )
            contingencies = [
                {
                    "contingency_id": "line_out",
                    "event_type": "simultaneous",
                    "order": 1,
                    "components": [{"type": "branch", "id": "branch_000001"}],
                }
            ]
            case_data = {"case_id": "case", "buses": [], "generators": [], "loads": [], "branches": []}
            adapter = _FakeAdapter()

            with patch.object(run_scopf, "parse_matpower_case", return_value=case_data):
                result = run_scopf._run_one_case(
                    repo_root,
                    args,
                    "case",
                    case_file,
                    "n1_set",
                    contingencies,
                    adapter,
                )

            self.assertTrue(result["ok"])
            attempt = Path(result["attempt_dir"])
            self.assertTrue((attempt / "SUCCESS").is_file())
            self.assertTrue((attempt / "manifests" / "checksums.sha256").is_file())
            resolved = json.loads((attempt / "inputs" / "resolved_contingency_set.json").read_text(encoding="utf-8"))
            self.assertEqual(resolved["contingency_set_id"], "n1_set")
            self.assertEqual(resolved["contingency_count"], 1)
            self.assertEqual(adapter.calls[0][2], {"timeout_s": 30.0, "tol": 1e-7, "max_iter": 100})
            registry = (repo_root / "runs" / "run_registry.jsonl").read_text(encoding="utf-8")
            self.assertIn('"task": "scopf"', registry)
            self.assertIn('"contingency_set_id": "n1_set"', registry)


if __name__ == "__main__":
    unittest.main()
