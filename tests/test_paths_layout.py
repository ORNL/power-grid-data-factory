"""Tests for the data-layout single source of truth and clean_workspace."""
from __future__ import annotations

import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from _bootstrap import REPO_ROOT  # noqa: F401  (ensures src on path)

from grid_data_factory.storage import paths


class PathsLayoutTests(unittest.TestCase):
    def test_tier_roots_are_disjoint_and_relative(self):
        roots = [paths.INPUTS, paths.DERIVED, paths.OUTPUTS, paths.SCRATCH, paths.REPORTS]
        self.assertEqual(len(roots), len(set(roots)))
        for root in roots:
            self.assertTrue(root.startswith("data/"))
            self.assertFalse(root.endswith("/"))

    def test_protected_and_cleanable_do_not_overlap(self):
        self.assertEqual(set(paths.CLEANABLE) & set(paths.PROTECTED), set())
        self.assertEqual(set(paths.REBUILDABLE) & set(paths.PROTECTED), set())

    def test_helpers_compose_under_repo_root(self):
        root = Path("/tmp/repo")
        self.assertEqual(paths.runs_root(root), root / "data/outputs/runs")
        self.assertEqual(paths.campaign_root(root, "c1"), root / "data/outputs/campaigns/c1")
        self.assertEqual(paths.canonical_dir(root), root / "data/derived/canonical")
        self.assertEqual(paths.topology_registry_dir(root), root / "data/derived/registries/topology")
        self.assertEqual(paths.operating_point_registry_dir(root), root / "data/derived/registries/operating_point")
        self.assertEqual(paths.tmp_dir(root), root / "data/scratch/tmp")
        self.assertEqual(paths.reports_dir(root), root / "data/reports")


class CleanWorkspaceTests(unittest.TestCase):
    def _make_tree(self, base: Path) -> None:
        for tier in (paths.OUTPUTS, paths.SCRATCH, paths.DERIVED, paths.INPUTS, paths.REPORTS):
            (base / tier).mkdir(parents=True, exist_ok=True)
            (base / tier / ".gitkeep").write_text("", encoding="utf-8")
        (base / paths.OUTPUTS / "runs" / "attempt_1").mkdir(parents=True)
        (base / paths.OUTPUTS / "runs" / "attempt_1" / "result.json").write_text("{}", encoding="utf-8")
        (base / paths.SCRATCH / "logs" / "job.out").parent.mkdir(parents=True, exist_ok=True)
        (base / paths.SCRATCH / "logs" / "job.out").write_text("log", encoding="utf-8")
        (base / paths.DERIVED / "canonical" / "case.json").parent.mkdir(parents=True, exist_ok=True)
        (base / paths.DERIVED / "canonical" / "case.json").write_text("{}", encoding="utf-8")
        (base / paths.INPUTS / "keep.txt").write_text("keep", encoding="utf-8")
        (base / paths.REPORTS / "report.json").write_text("{}", encoding="utf-8")

    def _run_clean(self, base: Path, *extra: str) -> subprocess.CompletedProcess:
        script = REPO_ROOT / "scripts" / "clean_workspace.py"
        return subprocess.run(
            [sys.executable, str(script), "--repo-root", str(base), *extra],
            capture_output=True,
            text=True,
            check=True,
        )

    def test_dry_run_removes_nothing(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            self._make_tree(base)
            self._run_clean(base, "--dry-run")
            self.assertTrue((base / paths.OUTPUTS / "runs" / "attempt_1" / "result.json").exists())
            self.assertTrue((base / paths.SCRATCH / "logs" / "job.out").exists())

    def test_default_clears_outputs_and_scratch_only(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            self._make_tree(base)
            self._run_clean(base)
            # Cleaned tiers: contents gone, root + .gitkeep kept.
            self.assertFalse((base / paths.OUTPUTS / "runs").exists())
            self.assertTrue((base / paths.OUTPUTS / ".gitkeep").exists())
            self.assertFalse((base / paths.SCRATCH / "logs").exists())
            self.assertTrue((base / paths.SCRATCH / ".gitkeep").exists())
            # Derived is rebuildable but untouched without --include-derived.
            self.assertTrue((base / paths.DERIVED / "canonical" / "case.json").exists())
            # Protected tiers untouched.
            self.assertTrue((base / paths.INPUTS / "keep.txt").exists())
            self.assertTrue((base / paths.REPORTS / "report.json").exists())

    def test_include_derived_clears_derived(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            self._make_tree(base)
            self._run_clean(base, "--include-derived")
            self.assertFalse((base / paths.DERIVED / "canonical").exists())
            self.assertTrue((base / paths.DERIVED / ".gitkeep").exists())
            self.assertTrue((base / paths.INPUTS / "keep.txt").exists())


if __name__ == "__main__":
    unittest.main()
