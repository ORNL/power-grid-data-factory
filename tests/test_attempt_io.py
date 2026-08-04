"""Tests for the shared attempt/registry I/O helpers."""
from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from _bootstrap import REPO_ROOT  # noqa: F401  (ensures src on path)

from grid_data_factory.storage import attempt_io


class UtcNowIsoTests(unittest.TestCase):
    def test_returns_timezone_aware_iso_string(self):
        value = attempt_io.utc_now_iso()
        self.assertIsInstance(value, str)
        self.assertIn("T", value)
        self.assertTrue(value.endswith("+00:00") or value.endswith("Z"))


class AppendRegistryRecordSafeTests(unittest.TestCase):
    def test_appends_jsonl_records(self):
        with tempfile.TemporaryDirectory() as tmp:
            runs_root = Path(tmp) / "runs"
            attempt_io.append_registry_record_safe(runs_root, {"run_id": "a", "task": "ac_opf"})
            attempt_io.append_registry_record_safe(runs_root, {"run_id": "b", "task": "ac_opf"})
            jsonl = runs_root / "run_registry.jsonl"
            self.assertTrue(jsonl.exists())
            lines = jsonl.read_text(encoding="utf-8").strip().splitlines()
            self.assertEqual(len(lines), 2)
            self.assertEqual(json.loads(lines[0])["run_id"], "a")
            self.assertEqual(json.loads(lines[1])["run_id"], "b")


class WriteCommonAttemptFilesTests(unittest.TestCase):
    def test_writes_expected_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            in_progress = Path(tmp) / "attempt"
            (in_progress / "environment").mkdir(parents=True)
            attempt_io.write_common_attempt_files(
                in_progress,
                {"solver_id": "x", "case_id": "case14"},
                ["--case", "case14"],
                "run_ac_opf.py",
            )
            self.assertEqual(
                (in_progress / "run.yaml").read_text(encoding="utf-8"),
                "solver_id: x\ncase_id: case14\n",
            )
            command_txt = (in_progress / "command.txt").read_text(encoding="utf-8")
            self.assertEqual(command_txt, "python scripts/run_ac_opf.py --case case14")
            command_json = json.loads((in_progress / "command.json").read_text(encoding="utf-8"))
            self.assertEqual(command_json["args"], ["scripts/run_ac_opf.py", "--case", "case14"])
            env_json = json.loads((in_progress / "environment" / "environment.json").read_text(encoding="utf-8"))
            self.assertEqual(env_json["script"], "run_ac_opf.py")
            self.assertEqual(env_json["python"], "3.11")


if __name__ == "__main__":
    unittest.main()
