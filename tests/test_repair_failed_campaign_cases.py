from __future__ import annotations

import importlib.util
import json
import tempfile
import unittest
from pathlib import Path

from _bootstrap import REPO_ROOT

_SPEC = importlib.util.spec_from_file_location(
    "repair_failed_campaign_cases",
    REPO_ROOT / "scripts" / "repair_failed_campaign_cases.py",
)
assert _SPEC and _SPEC.loader
_REPAIR = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(_REPAIR)


class RepairFailedCampaignCasesTests(unittest.TestCase):
    def _record(self, *, success: bool, status: str, candidate: dict, run_id: str = "run-1") -> dict:
        return {
            "schema_version": "1.1",
            "run_id": run_id,
            "candidate_id": candidate["candidate_id"],
            "task": "ac_opf",
            "case_id": candidate["case_id"],
            "solver_id": "solver",
            "success": success,
            "termination_status": status,
            "feasibility_label": "feasible" if success else "error",
            "objective": 1.0 if success else 0.0,
            "inputs": {"candidate": candidate, "resolved_case": {}},
            "result": {"success": success, "termination_status": status},
        }

    def test_prepare_and_merge_only_converged_retry(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            runs_root = root / "data/outputs/runs_3b"
            source = runs_root / "mapreduce_round_000/shard_00001/ac_opf/samples.jsonl"
            source.parent.mkdir(parents=True)
            candidate = {
                "candidate_id": "activsg2000::op::000001::ctg::000",
                "case_id": "activsg2000",
            }
            original = self._record(success=False, status="INVALID_MODEL", candidate=candidate)
            source.write_text(json.dumps(original) + "\n", encoding="utf-8")

            report_relpath = Path(
                "data/outputs/campaigns/ultrascale_3b__r000__s00001/"
                "round_summaries/round_000_ac_execution_report.json"
            )
            report = root / report_relpath
            report.parent.mkdir(parents=True)
            report.write_text(
                json.dumps(
                    {
                        "ok": True,
                        "solved": [
                            {
                                "candidate_id": candidate["candidate_id"],
                                "run_id": "run-1",
                                "success": False,
                                "termination_status": "INVALID_MODEL",
                            }
                        ],
                    }
                ),
                encoding="utf-8",
            )

            candidates_path = root / "repair/candidates.jsonl"
            prepare_manifest = root / "repair/prepare.json"
            prepared = _REPAIR.prepare(
                root,
                runs_root,
                [0],
                {"activsg2000", "pglib_opf_case300_ieee"},
                candidates_path,
                prepare_manifest,
            )
            self.assertEqual(prepared["extracted_failures"], 1)
            retry_candidate = json.loads(candidates_path.read_text(encoding="utf-8"))

            repair_runs = root / "repair/runs"
            retry_samples = repair_runs / "shard_00000/ac_opf/samples.jsonl"
            retry_samples.parent.mkdir(parents=True)
            retry = self._record(success=True, status="LOCALLY_SOLVED", candidate=retry_candidate)
            retry["objective"] = 123.0
            retry_samples.write_text(json.dumps(retry) + "\n", encoding="utf-8")

            merged = _REPAIR.merge(
                root,
                repair_runs,
                root / "repair/work",
                root / "repair/merge.json",
                True,
            )
            self.assertEqual(merged["rows_replaced"], 1)
            repaired = json.loads(source.read_text(encoding="utf-8"))
            self.assertTrue(repaired["success"])
            self.assertEqual(repaired["termination_status"], "LOCALLY_SOLVED")
            self.assertEqual(repaired["inputs"]["candidate"], candidate)
            self.assertEqual(repaired["repair_provenance"]["previous_termination_status"], "INVALID_MODEL")
            report_data = json.loads(report.read_text(encoding="utf-8"))
            self.assertTrue(report_data["solved"][0]["success"])
            self.assertEqual(report_data["solved"][0]["objective"], 123.0)
            self.assertTrue((root / "repair/work/backups" / source.relative_to(root)).exists())

    def test_failed_retry_does_not_modify_source(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "data/outputs/runs_3b/mapreduce_round_000/shard_00001/ac_opf/samples.jsonl"
            source.parent.mkdir(parents=True)
            candidate = {
                "candidate_id": "pglib_opf_case300_ieee::op::000001::ctg::000",
                "case_id": "pglib_opf_case300_ieee",
                "_repair_key": "key",
                "_repair_source_samples_relpath": str(source.relative_to(root)),
            }
            original = self._record(success=False, status="LOCALLY_INFEASIBLE", candidate={
                "candidate_id": candidate["candidate_id"],
                "case_id": candidate["case_id"],
            })
            original_text = json.dumps(original) + "\n"
            source.write_text(original_text, encoding="utf-8")
            retry_path = root / "repair/runs/shard_00000/ac_opf/samples.jsonl"
            retry_path.parent.mkdir(parents=True)
            retry_path.write_text(
                json.dumps(self._record(success=False, status="LOCALLY_INFEASIBLE", candidate=candidate)) + "\n",
                encoding="utf-8",
            )

            merged = _REPAIR.merge(
                root,
                root / "repair/runs",
                root / "repair/work",
                root / "repair/merge.json",
                True,
            )
            self.assertEqual(merged["rows_replaced"], 0)
            self.assertEqual(source.read_text(encoding="utf-8"), original_text)

    def test_prepare_all_cases_extracts_every_failure(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "data/outputs/runs_3b/mapreduce_round_000/shard_00001/ac_opf/samples.jsonl"
            source.parent.mkdir(parents=True)
            failed_a = self._record(
                success=False,
                status="LOCALLY_INFEASIBLE",
                candidate={"candidate_id": "case-a::1", "case_id": "case-a"},
                run_id="run-a",
            )
            failed_b = self._record(
                success=False,
                status="INVALID_MODEL",
                candidate={"candidate_id": "case-b::1", "case_id": "case-b"},
                run_id="run-b",
            )
            successful = self._record(
                success=True,
                status="LOCALLY_SOLVED",
                candidate={"candidate_id": "case-c::1", "case_id": "case-c"},
                run_id="run-c",
            )
            source.write_text(
                "".join(json.dumps(record) + "\n" for record in (failed_a, failed_b, successful)),
                encoding="utf-8",
            )

            prepared = _REPAIR.prepare(
                root,
                root / "data/outputs/runs_3b",
                [0],
                None,
                root / "repair/candidates.jsonl",
                root / "repair/prepare.json",
            )

            self.assertEqual(prepared["extracted_failures"], 2)
            self.assertEqual(prepared["counts_by_case"], {"case-a": 1, "case-b": 1})
            self.assertTrue(prepared["all_cases"])


if __name__ == "__main__":
    unittest.main()
