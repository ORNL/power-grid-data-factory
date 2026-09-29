from __future__ import annotations

import importlib.util
import json
import tempfile
import unittest
from pathlib import Path

from _bootstrap import REPO_ROOT
from grid_data_factory.campaigns.progress import SolveProgress
from grid_data_factory.campaigns.round_runner import _loaded_sample_progress

_SPEC = importlib.util.spec_from_file_location(
    "report_solve_progress",
    REPO_ROOT / "scripts" / "report_solve_progress.py",
)
assert _SPEC and _SPEC.loader
_REPORT = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(_REPORT)


class SolveProgressTests(unittest.TestCase):
    def test_resume_summary_and_progress_aggregation(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            samples = root / "runs/ac_opf/samples.jsonl"
            samples.parent.mkdir(parents=True)
            records = [
                {
                    "candidate_id": "a",
                    "case_id": "activsg2000",
                    "success": True,
                    "termination_status": "LOCALLY_SOLVED",
                },
                {
                    "candidate_id": "b",
                    "case_id": "pglib_opf_case300_ieee",
                    "success": False,
                    "termination_status": "process_error",
                },
            ]
            samples.write_text("".join(json.dumps(record) + "\n" for record in records), encoding="utf-8")

            done, initial = _loaded_sample_progress(
                root / "runs",
                retry_termination_statuses={"process_error"},
            )
            self.assertEqual(done, {"a"})
            self.assertEqual(initial["attempted"], 2)
            self.assertEqual(initial["converged_by_case"], {"activsg2000": 1})

            progress_dir = root / "progress"
            tracker = SolveProgress(progress_dir / "shard_00000.json", "repair", 3, initial, 3600)
            tracker.result(
                "pglib_opf_case300_ieee",
                "c",
                {"success": True, "termination_status": "LOCALLY_SOLVED"},
            )
            tracker.complete()

            summary = _REPORT.aggregate(progress_dir, 2)
            self.assertEqual(summary["started_shards"], 1)
            self.assertEqual(summary["complete_shards"], 1)
            self.assertEqual(summary["attempted"], 3)
            self.assertEqual(summary["converged"], 2)
            self.assertEqual(summary["resumed_attempts"], 2)
            self.assertEqual(summary["session_attempted"], 1)
            self.assertEqual(summary["session_converged"], 1)
            self.assertEqual(
                summary["converged_by_case"],
                {"activsg2000": 1, "pglib_opf_case300_ieee": 1},
            )


if __name__ == "__main__":
    unittest.main()
