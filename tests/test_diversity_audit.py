from __future__ import annotations

import json
import importlib.util
import tempfile
import unittest
from pathlib import Path

from _bootstrap import REPO_ROOT  # noqa: F401

from grid_data_factory.diversity.audit import (
    AuditConfig,
    DeterministicSample,
    audit_ledgers,
    compute_sample_metrics,
    discover_shard_ledgers,
    merge_audit_partials,
    scan_ledgers,
)
from grid_data_factory.diversity.nearest_neighbors import NUMERIC_FIELDS


def _row(candidate_id: str, value: float, topology: str = "baseline") -> dict:
    row = {field: value for field in NUMERIC_FIELDS}
    row.update(
        {
            "candidate_id": candidate_id,
            "run_id": f"run-{candidate_id}",
            "topology_class": topology,
            "contingency_order": 1,
            "active_constraint_signature": "branch:a",
            "near_active_constraint_signature": "branch:b",
            "dataset": "test",
            "round_index": 0,
        }
    )
    return row


class DeterministicSampleTests(unittest.TestCase):
    def test_sample_is_order_independent(self):
        rows = [_row(f"case::op::{index}", float(index)) for index in range(20)]
        left = DeterministicSample(5, 17)
        right = DeterministicSample(5, 17)
        for row in rows:
            left.add(row)
        for row in reversed(rows):
            right.add(row)
        self.assertEqual(
            {row["candidate_id"] for row in left.rows()},
            {row["candidate_id"] for row in right.rows()},
        )


class SampleMetricTests(unittest.TestCase):
    def test_detects_duplicate_descriptors_within_structural_group(self):
        rows = [_row("case::op::1", 0.0), _row("case::op::2", 0.0), _row("case::op::3", 10.0)]
        metrics = compute_sample_metrics(rows, near_duplicate_threshold=0.02, block_size=2)
        self.assertEqual(metrics["near_duplicate_count"], 2)
        self.assertAlmostEqual(metrics["exact_descriptor_duplicate_rate"], 1 / 3)
        self.assertLess(metrics["effective_sample_ratio"], 1.0)

    def test_does_not_compare_different_cases(self):
        rows = [_row("case_a::op::1", 0.0), _row("case_b::op::1", 0.0)]
        metrics = compute_sample_metrics(rows, near_duplicate_threshold=0.02)
        self.assertEqual(metrics["rows_with_comparable_neighbor"], 0)


class AuditIntegrationTests(unittest.TestCase):
    def test_discovers_and_audits_shard_ledgers(self):
        with tempfile.TemporaryDirectory() as tmp:
            campaigns = Path(tmp)
            ids_dir = campaigns / "campaign" / "round_summaries" / "round_000_shards"
            ids_dir.mkdir(parents=True)
            ids_dir.joinpath("shard_campaign_ids.txt").write_text("campaign__s00000\ncampaign__s00001\n", encoding="utf-8")
            for index in range(2):
                ledger = campaigns / f"campaign__s{index:05d}" / "diversity_ledger.parquet.jsonl"
                ledger.parent.mkdir(parents=True)
                ledger.write_text(json.dumps(_row(f"case::op::{index}", float(index))) + "\n", encoding="utf-8")

            ledgers, missing = discover_shard_ledgers(campaigns, "campaign", [0])
            self.assertEqual(len(ledgers), 2)
            self.assertEqual(missing, [])
            report, sample = audit_ledgers(ledgers, AuditConfig(sample_size=10, seed=3, block_size=2))
            self.assertEqual(report["row_count"], 2)
            self.assertEqual(len(sample), 2)
            joint = report["topology_contingency_metrics"]
            self.assertEqual(joint["occupied_cell_count"], 1)
            self.assertEqual(joint["cells"][0]["count"], 2)
            self.assertEqual(joint["cells"][0]["corpus_fraction"], 1.0)

    def test_partitioned_merge_matches_serial_audit(self):
        with tempfile.TemporaryDirectory() as tmp:
            ledgers = []
            for index in range(4):
                ledger = Path(tmp) / f"shard_{index}.jsonl"
                rows = [
                    _row(f"case::op::{index * 3 + offset}", float(index + offset), topology=f"topology_{index % 2}")
                    for offset in range(3)
                ]
                ledger.write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")
                ledgers.append(ledger)

            config = AuditConfig(sample_size=7, seed=11, block_size=3)
            serial_report, serial_sample = audit_ledgers(ledgers, config)
            partials = [scan_ledgers(ledgers[::2], config), scan_ledgers(ledgers[1::2], config)]
            merged_report, merged_sample = merge_audit_partials(partials, config)

            self.assertEqual(serial_report, merged_report)
            self.assertEqual(
                [row["candidate_id"] for row in serial_sample],
                [row["candidate_id"] for row in merged_sample],
            )


class MpiHelperTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        script = REPO_ROOT / "scripts" / "analyze_campaign_diversity_mpi.py"
        spec = importlib.util.spec_from_file_location("analyze_campaign_diversity_mpi", script)
        assert spec is not None and spec.loader is not None
        cls.module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(cls.module)

    def test_partition_paths_is_complete_and_disjoint(self):
        paths = [Path(str(index)) for index in range(11)]
        partitions = [self.module.partition_paths(paths, rank, 3) for rank in range(3)]
        self.assertEqual(set(item for partition in partitions for item in partition), set(paths))
        self.assertEqual(sum(len(partition) for partition in partitions), len(set().union(*map(set, partitions))))

    def test_global_priority_threshold(self):
        self.assertEqual(self.module.global_priority_threshold([[8, 2], [5, 1], [9]], 3), 5)
        self.assertEqual(self.module.global_priority_threshold([[], []], 3), None)


if __name__ == "__main__":
    unittest.main()