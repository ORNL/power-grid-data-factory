"""Tests for source-validation helpers."""
from __future__ import annotations

import tempfile
import unittest
import zipfile
from pathlib import Path

import yaml

from _bootstrap import REPO_ROOT  # noqa: F401  (ensures src on path)

from grid_data_factory.sources import validation


def _make_zip(path: Path, name: str = "case.m", body: str = "mpc.bus = [1];\n") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr(name, body)


class MaterialDetectionTests(unittest.TestCase):
    def test_pf_material(self):
        with tempfile.TemporaryDirectory() as t:
            base = Path(t)
            (base / "case.m").write_text("x", encoding="utf-8")
            self.assertTrue(validation.has_pf_material(base))

    def test_no_pf_material(self):
        with tempfile.TemporaryDirectory() as t:
            base = Path(t)
            (base / "notes.txt").write_text("x", encoding="utf-8")
            self.assertFalse(validation.has_pf_material(base))

    def test_opf_material_from_name(self):
        with tempfile.TemporaryDirectory() as t:
            base = Path(t)
            (base / "gencost.csv").write_text("x", encoding="utf-8")
            self.assertTrue(validation.has_opf_material(base))

    def test_opf_material_from_m_content(self):
        with tempfile.TemporaryDirectory() as t:
            base = Path(t)
            (base / "net.m").write_text("mpc.gencost = [2 0 0];", encoding="utf-8")
            self.assertTrue(validation.has_opf_material(base))


class ValidateCaseTests(unittest.TestCase):
    def test_missing_when_no_raw(self):
        with tempfile.TemporaryDirectory() as t:
            status, _ = validation.validate_case({"case_id": "c"}, Path(t) / "c")
            self.assertEqual(status, "MISSING")

    def test_downloaded_unregistered(self):
        with tempfile.TemporaryDirectory() as t:
            case_root = Path(t) / "c"
            (case_root / "raw").mkdir(parents=True)
            (case_root / "raw" / "a.zip").write_text("x", encoding="utf-8")
            status, details = validation.validate_case({"case_id": "c"}, case_root)
            self.assertEqual(status, "DOWNLOADED_UNREGISTERED")
            self.assertIn("a.zip", details["raw_files"])

    def test_validated_full_pipeline(self):
        with tempfile.TemporaryDirectory() as t:
            case_root = Path(t) / "c"
            raw = case_root / "raw"
            raw.mkdir(parents=True)
            _make_zip(raw / "bundle.zip")
            (case_root / "source_manifest.yaml").write_text(
                yaml.safe_dump({"archive": {"relative_path": "raw/bundle.zip"}}),
                encoding="utf-8",
            )
            extracted = case_root / "extracted"
            extracted.mkdir()
            (extracted / "net.m").write_text("mpc.gencost = [2 0 0];", encoding="utf-8")
            status, _ = validation.validate_case({"case_id": "c"}, case_root)
            self.assertEqual(status, "VALIDATED")


class ValidateSourcesTests(unittest.TestCase):
    def test_git_missing(self):
        with tempfile.TemporaryDirectory() as t:
            repo = Path(t)
            sources = {"s": {"type": "git", "destination": "repo_dir"}}
            result = validation.validate_sources(sources, repo, repo / "cfg.yaml")
            self.assertFalse(result["ok"])
            self.assertEqual(result["sources"]["s"]["status"], "MISSING")

    def test_archive_collection_checksum_mismatch(self):
        with tempfile.TemporaryDirectory() as t:
            repo = Path(t)
            raw = repo / "dst" / "raw"
            raw.mkdir(parents=True)
            _make_zip(raw / "a.zip")
            sources = {
                "s": {
                    "type": "archive_collection",
                    "destination": "dst",
                    "downloads": [{"url": "http://x/a.zip", "expected_sha256": "deadbeef"}],
                }
            }
            result = validation.validate_sources(sources, repo, repo / "cfg.yaml")
            self.assertFalse(result["ok"])
            self.assertEqual(result["sources"]["s"]["status"], "CHECKSUM_MISMATCH")


if __name__ == "__main__":
    unittest.main()
