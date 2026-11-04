from __future__ import annotations

import hashlib
import importlib.util
import json
import tempfile
import unittest
from pathlib import Path

from _bootstrap import REPO_ROOT  # noqa: F401


class OrphanPvAuditTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        script = REPO_ROOT / "scripts" / "audit_orphan_pv_pf_corpus.py"
        spec = importlib.util.spec_from_file_location("audit_orphan_pv_pf_corpus", script)
        assert spec is not None and spec.loader is not None
        cls.module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(cls.module)

    def test_parse_sample_lines_recovers_suffix_and_counts_malformed_line(self):
        valid_record = {"task": "pf", "success": True, "candidate_id": "valid", "run_id": "run-valid"}
        suffix_record = {"task": "pf", "success": False, "candidate_id": "recovered", "run_id": "run-recovered"}
        records, hashes, malformed_lines = self.module.parse_sample_lines(
            [json.dumps(valid_record), "damaged-prefix" + json.dumps(suffix_record), "not-json"]
        )

        self.assertEqual(records, [valid_record, suffix_record])
        self.assertEqual(
            hashes,
            {
                self.module.canonical_record_hash(valid_record),
                self.module.canonical_record_hash(suffix_record),
            },
        )
        self.assertEqual(malformed_lines, 2)

    def test_parse_sample_lines_rejects_nested_fragment_without_pf_identity(self):
        records, hashes, malformed_lines = self.module.parse_sample_lines(
            ['{"success": true, "inputs": {}']
        )

        self.assertEqual(records, [])
        self.assertEqual(hashes, set())
        self.assertEqual(malformed_lines, 1)

    def test_valid_primary_source_hash_deduplicates_overlay_record(self):
        record = {"task": "pf", "success": True, "candidate_id": "primary", "run_id": "run-primary"}
        row_hash = self.module.canonical_record_hash(record)

        with tempfile.TemporaryDirectory() as tmp:
            sample_path = Path(tmp) / "samples.jsonl"
            sample_path.write_text(json.dumps(record) + "\n", encoding="utf-8")
            source_hashes = self.module.source_hashes_for_overlay_file(sample_path, {sample_path.resolve()})

        overlay_by_campaign, deduplicated = self.module.select_overlay_records(
            [("riker_pf_test", sample_path, row_hash, record)],
            source_hashes,
            {"riker_pf_test"},
        )
        self.assertEqual(dict(overlay_by_campaign), {})
        self.assertEqual(deduplicated, 1)

    def test_load_recovery_overlay_checks_manifest_digest_and_record_hash(self):
        record = {"task": "pf", "success": False, "candidate_id": "overlay", "run_id": "run-overlay"}
        row_hash = self.module.canonical_record_hash(record)
        overlay_bytes = (json.dumps(record, separators=(",", ":")) + "\n").encode("utf-8")
        manifest = {
            "record_count": 1,
            "records_sha256": hashlib.sha256(overlay_bytes).hexdigest(),
            "canonical_record_hash": "SHA-256 of UTF-8 JSON with sorted keys and compact separators",
            "records": [
                {
                    "sha256": row_hash,
                    "source_shard": "data/outputs/runs/riker_pf_test/mapreduce_round_000/shard_00001/pf",
                }
            ],
        }

        with tempfile.TemporaryDirectory() as tmp:
            overlay_path = Path(tmp) / "samples.jsonl"
            manifest_path = Path(tmp) / "recovery_manifest.json"
            runs_root = Path(tmp) / "relocated-runs"
            source_path = runs_root / "riker_pf_test/mapreduce_round_000/shard_00001/pf/samples.jsonl"
            source_path.parent.mkdir(parents=True)
            source_path.write_text(json.dumps(record) + "\n", encoding="utf-8")
            overlay_path.write_bytes(overlay_bytes)
            manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

            entries = self.module.load_recovery_overlay(overlay_path, manifest_path, runs_root)
            self.assertEqual(
                entries,
                [("riker_pf_test", source_path, row_hash, record)],
            )
            source_hashes = self.module.source_hashes_for_overlay_file(source_path, {source_path.resolve()})
            overlay_by_campaign, deduplicated = self.module.select_overlay_records(
                entries, source_hashes, {"riker_pf_test"}
            )
            self.assertEqual(dict(overlay_by_campaign), {})
            self.assertEqual(deduplicated, 1)

            overlay_path.write_bytes(overlay_bytes + b" ")
            with self.assertRaisesRegex(ValueError, "digest does not match"):
                self.module.load_recovery_overlay(overlay_path, manifest_path)


if __name__ == "__main__":
    unittest.main()