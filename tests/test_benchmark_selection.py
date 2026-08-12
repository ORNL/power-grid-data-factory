from __future__ import annotations

import json
from pathlib import Path
import tempfile
import unittest

from _bootstrap import REPO_ROOT  # noqa: F401

from grid_data_factory.campaigns.benchmark_selection import (
    candidate_identity,
    load_candidate_ids_by_status,
    load_completed_candidate_ids,
    select_unfinished_candidates,
)


class BenchmarkSelectionTests(unittest.TestCase):
    def test_extracts_candidate_identity_from_prefix(self) -> None:
        line = json.dumps({"candidate_id": "case-a::7", "case_id": "case-a", "payload": {"case_id": "nested"}})
        self.assertEqual(candidate_identity(line), ("case-a::7", "case-a"))

    def test_loads_ids_across_shards_and_tolerates_truncated_record(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            for shard, candidate_id in (("00000", "done-a"), ("00001", "done-b")):
                path = root / f"shard_{shard}" / "ac_opf" / "samples.jsonl"
                path.parent.mkdir(parents=True)
                path.write_text(json.dumps({"candidate_id": candidate_id}) + "\n{\"candidate", encoding="utf-8")

            completed, stats = load_completed_candidate_ids(root)

        self.assertEqual(completed, {"done-a", "done-b"})
        self.assertEqual(stats["files_scanned"], 2)
        self.assertEqual(stats["records_seen"], 4)
        self.assertEqual(stats["malformed_records"], 2)
        self.assertGreater(stats["bytes_read"], 0)

    def test_selects_bounded_deterministic_unfinished_candidates_per_case(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "candidates.jsonl"
            rows = [
                {"candidate_id": f"{case_id}::{index}", "case_id": case_id, "value": index}
                for case_id in ("case-a", "case-b")
                for index in range(10)
            ]
            path.write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")

            first, stats = select_unfinished_candidates(path, {"case-a::3", "case-b::8"}, per_case=4, seed=17)
            second, _ = select_unfinished_candidates(path, {"case-a::3", "case-b::8"}, per_case=4, seed=17)

        self.assertEqual(first, second)
        self.assertEqual({case_id: len(lines) for case_id, lines in first.items()}, {"case-a": 4, "case-b": 4})
        selected_ids = {json.loads(line)["candidate_id"] for lines in first.values() for line in lines}
        self.assertFalse(selected_ids & {"case-a::3", "case-b::8"})
        self.assertEqual(stats["completed_records_skipped"], 2)
        self.assertEqual(stats["malformed_records"], 0)

    def test_loads_exact_timeout_ids_for_requested_cases(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "runs" / "shard" / "ac_opf" / "samples.jsonl"
            path.parent.mkdir(parents=True)
            rows = [
                {"candidate_id": "a::1", "case_id": "a", "termination_status": "timeout"},
                {"candidate_id": "a::2", "case_id": "a", "termination_status": "LOCALLY_SOLVED"},
                {"candidate_id": "b::1", "case_id": "b", "termination_status": "timeout"},
            ]
            path.write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")

            selected, stats = load_candidate_ids_by_status(path.parents[2], {"timeout"}, {"a"})

        self.assertEqual(selected, {"a": {"a::1"}})
        self.assertEqual(stats["matched_records"], 1)
        self.assertEqual(stats["malformed_records"], 0)


if __name__ == "__main__":
    unittest.main()