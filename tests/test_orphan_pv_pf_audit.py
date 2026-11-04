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
        valid_record = {"success": True, "candidate_id": "valid"}
        suffix_record = {"success": False, "candidate_id": "recovered"}
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

    def test_load_recovery_overlay_checks_manifest_digest_and_record_hash(self):
        record = {"success": False, "candidate_id": "overlay"}
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
            overlay_path.write_bytes(overlay_bytes)
            manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

            self.assertEqual(
                self.module.load_recovery_overlay(overlay_path, manifest_path),
                [("riker_pf_test", row_hash, record)],
            )

            overlay_path.write_bytes(overlay_bytes + b" ")
            with self.assertRaisesRegex(ValueError, "digest does not match"):
                self.module.load_recovery_overlay(overlay_path, manifest_path)


if __name__ == "__main__":
    unittest.main()