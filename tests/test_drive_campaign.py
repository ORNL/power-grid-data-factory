"""Tests for the top-level campaign driver (drive_campaign.py)."""
from __future__ import annotations

import importlib.util
import json
import tempfile
import unittest
from pathlib import Path

from _bootstrap import REPO_ROOT  # noqa: F401  (ensures src on path)


def _load_driver():
    path = REPO_ROOT / "scripts" / "drive_campaign.py"
    spec = importlib.util.spec_from_file_location("drive_campaign", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


_DRV = _load_driver()


def _write_marker(repo_root: Path, campaign_id: str, round_index: int, ok: bool) -> None:
    marker = _DRV.reduce_marker_path(repo_root, campaign_id, round_index)
    marker.parent.mkdir(parents=True, exist_ok=True)
    marker.write_text(json.dumps({"ok": ok, "round_index": round_index}), encoding="utf-8")


class RoundCompletionTests(unittest.TestCase):
    def test_missing_marker_is_incomplete(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.assertFalse(_DRV.round_complete(Path(tmp), "camp", 0))

    def test_ok_marker_is_complete(self):
        with tempfile.TemporaryDirectory() as tmp:
            _write_marker(Path(tmp), "camp", 0, ok=True)
            self.assertTrue(_DRV.round_complete(Path(tmp), "camp", 0))

    def test_not_ok_marker_is_incomplete(self):
        with tempfile.TemporaryDirectory() as tmp:
            _write_marker(Path(tmp), "camp", 0, ok=False)
            self.assertFalse(_DRV.round_complete(Path(tmp), "camp", 0))

    def test_corrupt_marker_is_incomplete(self):
        with tempfile.TemporaryDirectory() as tmp:
            marker = _DRV.reduce_marker_path(Path(tmp), "camp", 0)
            marker.parent.mkdir(parents=True, exist_ok=True)
            marker.write_text("{not json", encoding="utf-8")
            self.assertFalse(_DRV.round_complete(Path(tmp), "camp", 0))


class FirstIncompleteTests(unittest.TestCase):
    def test_first_gap_is_returned(self):
        with tempfile.TemporaryDirectory() as tmp:
            _write_marker(Path(tmp), "camp", 0, ok=True)
            _write_marker(Path(tmp), "camp", 1, ok=True)
            # round 2 missing
            self.assertEqual(_DRV.first_incomplete_round(Path(tmp), "camp", 10), 2)

    def test_partial_round_is_resume_point(self):
        with tempfile.TemporaryDirectory() as tmp:
            _write_marker(Path(tmp), "camp", 0, ok=True)
            _write_marker(Path(tmp), "camp", 1, ok=False)  # started, not ok
            self.assertEqual(_DRV.first_incomplete_round(Path(tmp), "camp", 5), 1)

    def test_all_complete_returns_none(self):
        with tempfile.TemporaryDirectory() as tmp:
            for r in range(3):
                _write_marker(Path(tmp), "camp", r, ok=True)
            self.assertIsNone(_DRV.first_incomplete_round(Path(tmp), "camp", 3))


class BudgetTests(unittest.TestCase):
    def test_total_budget_splits_and_sums(self):
        budgets = _DRV.compute_round_budgets(60_000_000, 0, 10, "constant", 1.0)
        self.assertEqual(len(budgets), 10)
        self.assertEqual(sum(budgets), 60_000_000)

    def test_per_round_budget_is_constant(self):
        self.assertEqual(_DRV.compute_round_budgets(0, 500, 4, "constant", 1.0), [500, 500, 500, 500])


class EnvTests(unittest.TestCase):
    def test_build_submit_env_sets_resume_and_budget(self):
        env = _DRV.build_submit_env({"PATH": "/bin"}, "camp", 3, 6_000_000, resume=True, extra_env={"MAX_K": "10"})
        self.assertEqual(env["CAMPAIGN_ID"], "camp")
        self.assertEqual(env["ROUND_INDEX"], "3")
        self.assertEqual(env["RESUME"], "1")
        self.assertEqual(env["BUDGET"], "6000000")
        self.assertEqual(env["MAX_K"], "10")
        self.assertEqual(env["PATH"], "/bin")

    def test_reserved_keys_win_over_extra_env(self):
        env = _DRV.build_submit_env({}, "camp", 1, 100, resume=True, extra_env={"ROUND_INDEX": "999"})
        self.assertEqual(env["ROUND_INDEX"], "1")

    def test_parse_extra_env_rejects_bad_pairs(self):
        with self.assertRaises(ValueError):
            _DRV.parse_extra_env(["NOEQUALS"])
        self.assertEqual(_DRV.parse_extra_env(["A=1", "B=x=y"]), {"A": "1", "B": "x=y"})


class ResourceFlagTests(unittest.TestCase):
    def test_empty_when_defaults(self):
        self.assertEqual(_DRV.build_resource_flags(0, 0, 0, ""), [])

    def test_full_override(self):
        self.assertEqual(
            _DRV.build_resource_flags(64, 16, 1, "36:00:00"),
            ["--nodes", "64", "--ntasks-per-node", "16", "--cpus-per-task", "1", "--time", "36:00:00"],
        )

    def test_partial_override(self):
        self.assertEqual(_DRV.build_resource_flags(64, 0, 0, "36:00:00"), ["--nodes", "64", "--time", "36:00:00"])


if __name__ == "__main__":
    unittest.main()
