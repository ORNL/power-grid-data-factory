from __future__ import annotations

import importlib.util
import tempfile
import unittest
from pathlib import Path

from _bootstrap import REPO_ROOT


SCRIPT = REPO_ROOT / "scripts" / "reduce_pf_campaign_shards.py"
SPEC = importlib.util.spec_from_file_location("reduce_pf_campaign_shards", SCRIPT)
assert SPEC and SPEC.loader
REDUCER = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(REDUCER)


class ReducePFCampaignShardsTests(unittest.TestCase):
    def test_atomic_rebuild_replaces_previous_aggregate_rows(self):
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary) / "ledger.parquet.jsonl"
            REDUCER.write_atomic_jsonl(output, [{"candidate_id": "old"}], retain_existing=False)
            REDUCER.write_atomic_jsonl(output, [{"candidate_id": "replayed"}], retain_existing=False)

            self.assertEqual(output.read_text(), '{"candidate_id": "replayed"}\n')

    def test_additional_runs_root_replaces_matching_original_shard(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            original = root / "original"
            staged = root / "staged"
            original_files = [
                original / "mapreduce_round_003/shard_00040/pf/samples.jsonl",
                original / "mapreduce_round_003/shard_00041/pf/samples.jsonl",
            ]
            staged_file = staged / "mapreduce_round_003/shard_00040/pf/samples.jsonl"
            for sample_file in [*original_files, staged_file]:
                sample_file.parent.mkdir(parents=True, exist_ok=True)
                sample_file.write_text("{}\n")

            selected = REDUCER.sample_files_for_roots(original, [staged], 3)

            self.assertEqual(selected, [original_files[1], staged_file])


if __name__ == "__main__":
    unittest.main()