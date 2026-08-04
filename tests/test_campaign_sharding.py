"""Tests for the candidate sharding + coverage-backfill helpers."""
from __future__ import annotations

import unittest

from _bootstrap import REPO_ROOT  # noqa: F401  (ensures src on path)

from grid_data_factory.campaigns import sharding


class SafeFloatTests(unittest.TestCase):
    def test_values(self):
        self.assertEqual(sharding.safe_float("1.5"), 1.5)
        self.assertEqual(sharding.safe_float(None), 0.0)
        self.assertEqual(sharding.safe_float("nope"), 0.0)


class DeriveDatasetTests(unittest.TestCase):
    def test_explicit_key_wins(self):
        self.assertEqual(sharding.derive_dataset({"dataset": "custom", "case_id": "pglib_x"}), "custom")

    def test_case_id_inference(self):
        self.assertEqual(sharding.derive_dataset({"case_id": "pglib_opf_case14"}), "pglib")
        self.assertEqual(sharding.derive_dataset({"case_id": "ACTIVSg2000"}), "tamu")
        self.assertEqual(sharding.derive_dataset({"case_id": "rts_gmlc"}), "rts_gmlc")
        self.assertEqual(sharding.derive_dataset({"case_id": "weird"}), "unknown")


class BucketValueTests(unittest.TestCase):
    def test_topology_fallback(self):
        self.assertEqual(sharding.bucket_value({"topology_id": "t1"}, "topology_id"), "t1")
        self.assertEqual(sharding.bucket_value({"contingency_class": "n1"}, "topology_id"), "n1")
        self.assertEqual(sharding.bucket_value({}, "topology_id"), "none")

    def test_generic_key(self):
        self.assertEqual(sharding.bucket_value({"operating_regime": "peak"}, "operating_regime"), "peak")
        self.assertEqual(sharding.bucket_value({}, "operating_regime"), "unknown")


class CoverageTests(unittest.TestCase):
    def test_missing_buckets(self):
        selected = [{"candidate_id": "a", "operating_regime": "peak"}]
        pool = selected + [{"candidate_id": "b", "operating_regime": "offpeak"}]
        universe = sharding.coverage_universe(selected, pool, ["operating_regime"])
        missing = sharding.missing_buckets(selected, universe, ["operating_regime"], 1)
        self.assertEqual(missing, {"operating_regime": ["offpeak"]})


class BackfillTests(unittest.TestCase):
    def test_backfill_adds_missing_bucket(self):
        selected = [{"candidate_id": "a", "operating_regime": "peak"}]
        pool = selected + [
            {"candidate_id": "b", "operating_regime": "offpeak", "novelty_score": 0.2},
            {"candidate_id": "c", "operating_regime": "offpeak", "novelty_score": 0.9},
        ]
        out, added, before, after = sharding.backfill_coverage(
            selected, pool, ["operating_regime"], min_per_bucket=1, max_additions=0, score_key="novelty_score"
        )
        self.assertEqual(added, ["c"])  # highest novelty wins
        self.assertEqual(before, {"operating_regime": ["offpeak"]})
        self.assertEqual(after, {})
        self.assertEqual(len(out), 2)

    def test_max_additions_cap(self):
        selected: list[dict] = []
        pool = [
            {"candidate_id": "a", "operating_regime": "peak", "novelty_score": 0.5},
            {"candidate_id": "b", "operating_regime": "offpeak", "novelty_score": 0.4},
        ]
        out, added, _before, after = sharding.backfill_coverage(
            selected, pool, ["operating_regime"], min_per_bucket=1, max_additions=1, score_key="novelty_score"
        )
        self.assertEqual(len(added), 1)
        self.assertNotEqual(after, {})  # still missing one bucket


class ShardRowsTests(unittest.TestCase):
    def test_deterministic_round_robin(self):
        rows = [{"candidate_id": c} for c in ["c", "a", "b", "d"]]
        shards = sharding.shard_rows(rows, 2)
        self.assertEqual([r["candidate_id"] for r in shards[0]], ["a", "c"])
        self.assertEqual([r["candidate_id"] for r in shards[1]], ["b", "d"])
        # Stable across runs.
        self.assertEqual(sharding.shard_rows(rows, 2), shards)


if __name__ == "__main__":
    unittest.main()
