from __future__ import annotations

import importlib.util
import json
import tempfile
import unittest
from pathlib import Path

from _bootstrap import REPO_ROOT


SCRIPT = REPO_ROOT / "scripts" / "reduce_campaign_shards.py"
SPEC = importlib.util.spec_from_file_location("reduce_campaign_shards", SCRIPT)
assert SPEC and SPEC.loader
REDUCER = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(REDUCER)


class ReduceCampaignShardsTests(unittest.TestCase):
    def _write_shard(self, root: Path, shard_id: str, solved: int, failed: int) -> None:
        shard = root / "data" / "outputs" / "campaigns" / shard_id
        summaries = shard / "round_summaries"
        summaries.mkdir(parents=True)
        (summaries / "round_005_ac_execution_report.json").write_text(json.dumps({
            "ok": True,
            "input_candidate_count": solved + failed + 1,
            "solved_count": solved,
            "failed_count": failed,
            "skipped_count": 1,
        }))
        (shard / "diversity_ledger.parquet.jsonl").write_text('{"candidate_id":"x"}\n')

    def _fixture(self, root: Path) -> Path:
        (root / "configs").mkdir()
        (root / "configs" / "campaign.yaml").write_text("acquisition_budget: {}\n")
        self._write_shard(root, "campaign__s00001", solved=2, failed=1)
        self._write_shard(root, "campaign__s00000", solved=3, failed=4)
        ids = root / "ids.txt"
        ids.write_text("campaign__s00001\ncampaign__s00000\n")
        return ids

    def test_publishes_manifest_without_mutating_cumulative_ledgers(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            ids = self._fixture(root)
            campaign_root = root / "data" / "outputs" / "campaigns" / "campaign"
            campaign_root.mkdir(parents=True)
            cumulative = campaign_root / "diversity_ledger.parquet.jsonl"
            cumulative.write_text("existing\n")
            summary = REDUCER.reduce_shards(root, "campaign", 5, "configs/campaign.yaml", ids)
            self.assertTrue(summary["ok"])
            self.assertEqual(summary["shard_campaign_ids"], ["campaign__s00000", "campaign__s00001"])
            self.assertEqual(summary["merged_counts"], {
                "input_candidates": 12, "solved": 5, "failed": 5, "skipped": 2,
            })
            self.assertEqual(len(summary["ledger_fragments"]["diversity_ledger.parquet"]), 2)
            self.assertEqual(cumulative.read_text(), "existing\n")
            self.assertFalse((campaign_root / "round_summaries" / "round_005_reduce_state.json").exists())

    def test_resumes_checkpoint_without_recounting(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            ids = self._fixture(root)
            summaries = root / "data" / "outputs" / "campaigns" / "campaign" / "round_summaries"
            summaries.mkdir(parents=True)
            shard_ids = ["campaign__s00000", "campaign__s00001"]
            state = REDUCER._initial_state("campaign", 5, shard_ids)
            state.update({
                "next_shard_index": 1,
                "input_candidates": 8,
                "solved": 3,
                "failed": 4,
                "skipped": 1,
            })
            state["ledger_fragments"]["diversity_ledger.parquet"] = [
                str(root / "data" / "outputs" / "campaigns" / shard_ids[0] / "diversity_ledger.parquet.jsonl")
            ]
            REDUCER._atomic_write_json(summaries / "round_005_reduce_state.json", state)
            summary = REDUCER.reduce_shards(root, "campaign", 5, "configs/campaign.yaml", ids)
            self.assertEqual(summary["merged_counts"], {
                "input_candidates": 12, "solved": 5, "failed": 5, "skipped": 2,
            })
            self.assertEqual(len(summary["ledger_fragments"]["diversity_ledger.parquet"]), 2)

    def test_missing_report_blocks_without_advancing_checkpoint(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "configs").mkdir()
            (root / "configs" / "campaign.yaml").write_text("acquisition_budget: {}\n")
            ids = root / "ids.txt"
            ids.write_text("campaign__s00000\n")
            summary = REDUCER.reduce_shards(root, "campaign", 5, "configs/campaign.yaml", ids)
            state_path = (
                root / "data" / "outputs" / "campaigns" / "campaign"
                / "round_summaries" / "round_005_reduce_state.json"
            )
            state = json.loads(state_path.read_text())
            self.assertFalse(summary["ok"])
            self.assertEqual(state["next_shard_index"], 0)
            self._write_shard(root, "campaign__s00000", solved=2, failed=1)
            resumed = REDUCER.reduce_shards(root, "campaign", 5, "configs/campaign.yaml", ids)
            self.assertTrue(resumed["ok"])
            self.assertEqual(resumed["missing_shard_reports"], [])


if __name__ == "__main__":
    unittest.main()
