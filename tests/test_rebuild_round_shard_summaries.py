from __future__ import annotations

import importlib.util
import json
import tempfile
import unittest
from pathlib import Path

from _bootstrap import REPO_ROOT

SPEC = importlib.util.spec_from_file_location(
    "rebuild_round_shard_summaries", REPO_ROOT / "scripts" / "rebuild_round_shard_summaries.py"
)
assert SPEC and SPEC.loader
REBUILD = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(REBUILD)

BANDS = {"secure": {"minimum_margin": 0.0}, "insecure": {"maximum_margin": 0.0}}
POLICY = {"cases": {"bigcase": {"enabled": False, "tier": "off"}}}


def _sample(candidate_id: str, pg: float, *, convention: str | None = "per_unit_scaled_v2", success=True,
            objective: float | None = 10.0) -> dict:
    case = {
        "case_id": "tiny", "base_mva": 100.0,
        "buses": [{"bus_id": "1", "vmin": 0.95, "vmax": 1.05}],
        "generators": [{"gen_id": "g1", "pmin": 0.0, "pmax": 100.0, "qmin": -50.0, "qmax": 50.0,
                        "cost": [0.1, 20.0, 0.0], "status": 1}],
        "branches": [{"branch_id": "b1", "rate_a": 100.0}],
        "loads": [{"pd": 50.0, "qd": 10.0}],
    }
    result = {
        "success": success, "termination_status": "LOCALLY_SOLVED" if success else "LOCALLY_INFEASIBLE",
        "solver_name": "powermodels", "objective": objective,
        "raw_result": {"solution": {"gen": {"1": {"pg": pg, "qg": 0.0}}, "bus": {"1": {"vm": 1.0}},
                                    "branch": {"1": {"pf": pg, "pt": -pg}}}},
    }
    if convention:
        result["cost_convention"] = convention
    return {
        "candidate_id": candidate_id, "run_id": f"run-{candidate_id}", "task": "ac_opf", "case_id": "tiny",
        "success": success, "termination_status": result["termination_status"], "objective": objective,
        "inputs": {"resolved_case": case, "candidate": {"candidate_id": candidate_id, "case_id": "tiny",
                                                         "grid_family": "toy", "dataset": "toy"}},
        "result": result,
    }


class RebuildRoundShardSummariesTests(unittest.TestCase):
    def _fixture(self, root: Path, samples: list[dict]) -> Path:
        summaries = root / "data/outputs/campaigns/camp/round_summaries"
        shards = summaries / "round_002_shards"
        shards.mkdir(parents=True)
        (shards / "shard_campaign_ids.txt").write_text("camp__r002__s00007\n")
        selected = [{"candidate_id": cid, "case_id": "tiny"} for cid in dict.fromkeys(s["candidate_id"] for s in samples)]
        selected += [{"candidate_id": "skip1", "case_id": "bigcase"}, {"candidate_id": "lost", "case_id": "tiny"}]
        (shards / "selected_00007.jsonl").write_text("".join(json.dumps(c) + "\n" for c in selected))
        shard_root = root / "data/outputs/campaigns/camp__r002__s00007"
        (shard_root / "round_summaries").mkdir(parents=True)
        (shard_root / "round_summaries/round_002_ac_execution_report.json").write_text(json.dumps({
            "ok": True, "solved": [], "max_failure_fraction": 0.4,
            "failed": [{"candidate_id": "lost", "error": "RuntimeError: boom"}],
        }))
        (shard_root / "diversity_ledger.parquet.jsonl").write_text('{"stale": true}\n' * 5)
        runs = root / "runs/mapreduce_round_002/shard_00007/ac_opf"
        runs.mkdir(parents=True)
        (runs / "samples.jsonl").write_text("".join(json.dumps(s) + "\n" for s in samples) + "{trunc\n")
        return shard_root

    def test_rebuilds_report_and_ledgers_from_samples(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            samples = [_sample("a", 0.4), _sample("b", 0.999), _sample("a", 0.6),
                       _sample("t", 0.0, convention=None, success=False, objective=None)]
            samples[3]["result"]["raw_result"] = {}
            shard_root = self._fixture(root, samples)
            counts = REBUILD.rebuild_shard(root, "camp", 2, "camp__r002__s00007", root / "runs", BANDS, POLICY,
                                           "policy.yaml", 1800.0)
            self.assertEqual(counts["records"], 4)
            self.assertEqual(counts["duplicate_candidate_rows"], 1)
            self.assertEqual(counts["malformed_rows"], 1)
            self.assertEqual(counts["legacy_cost_records"], 0)
            self.assertEqual(counts["unmarked_without_solution"], 1)

            report = json.loads((shard_root / "round_summaries/round_002_ac_execution_report.json").read_text())
            self.assertEqual(report["input_candidate_count"], 5)
            self.assertEqual((report["solved_count"], report["failed_count"], report["skipped_count"]), (3, 1, 1))
            self.assertEqual(report["unsampled_candidate_count"], 0)
            self.assertEqual(report["max_failure_fraction"], 0.4)
            rows = {row["candidate_id"]: row for row in report["solved"]}
            self.assertEqual(list(rows), ["a", "b", "t"])
            self.assertAlmostEqual(rows["a"]["security_margin"], 0.05, places=6)

            diversity = [json.loads(line) for line in (shard_root / "diversity_ledger.parquet.jsonl").open()]
            self.assertEqual([row["candidate_id"] for row in diversity], ["a", "b", "t"])
            self.assertAlmostEqual(diversity[0]["generator_p_p90"], 60.0)
            self.assertEqual(diversity[0]["duplicate_status"], "new")
            self.assertEqual(diversity[0]["round_index"], 2)
            active = {(r["constraint_family"], r["component_id"]): r for r in
                      (json.loads(line) for line in (shard_root / "active_constraint_ledger.parquet.jsonl").open())}
            self.assertEqual(active[("generator_pmax", "g1")]["near_active_count"], 1)
            self.assertEqual(active[("generator_pmin", "g1")]["active_count"], 1)
            boundary = (shard_root / "security_boundary_ledger.parquet.jsonl").read_text().splitlines()
            self.assertEqual(len(boundary), 3)

    def test_finalize_requires_all_parts_and_no_legacy_costs(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            self._fixture(root, [_sample("a", 0.4), _sample("q", 0.5, convention=None)])
            (root / "configs").mkdir()
            (root / "configs/c.yaml").write_text("security_margin_bands: {}\n")
            work = root / "work"
            first = REBUILD.run_part(root, "camp", [2], root / "runs", "configs/c.yaml", None, 1800.0, work, 0, 2)
            self.assertEqual(first["legacy_cost_records"], 1)
            self.assertFalse(REBUILD.finalize(work, 2, work / "out.json")["complete"])
            REBUILD.run_part(root, "camp", [2], root / "runs", "configs/c.yaml", None, 1800.0, work, 1, 2)
            summary = REBUILD.finalize(work, 2, work / "out.json")
            self.assertEqual(summary["missing_parts"], [])
            self.assertFalse(summary["complete"])
            again = REBUILD.run_part(root, "camp", [2], root / "runs", "configs/c.yaml", None, 1800.0, work, 0, 2)
            self.assertEqual(again, first)


if __name__ == "__main__":
    unittest.main()
