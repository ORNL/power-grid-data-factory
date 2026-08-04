"""Tests for source-acquisition helpers (offline-safe subset)."""
from __future__ import annotations

import tempfile
import unittest
import zipfile
from pathlib import Path

from _bootstrap import REPO_ROOT  # noqa: F401  (ensures src on path)

from grid_data_factory.sources import download


class ClassificationTests(unittest.TestCase):
    def test_source_type(self):
        self.assertEqual(download.source_type({"type": "git"}), "git")
        self.assertEqual(download.source_type({"repository": "x"}), "git")
        self.assertEqual(download.source_type({"downloads": [{}]}), "archive_collection")
        self.assertEqual(download.source_type({}), "manual_catalog")

    def test_destination_dir(self):
        self.assertEqual(download.destination_dir({"destination": "a"}), "a")
        self.assertEqual(download.destination_dir({"target_dir": "b"}), "b")

    def test_source_url(self):
        self.assertEqual(download.source_url({"repository": "r"}), "r")
        self.assertEqual(download.source_url({"url": "u"}), "u")
        self.assertIsNone(download.source_url({}))


class IterDownloadSpecsTests(unittest.TestCase):
    def test_merges_downloads_and_direct_cases(self):
        spec = {
            "downloads": [{"url": "http://a/x.zip"}],
            "cases": [
                {"archive_url": "http://a/y.zip", "acquisition_mode": "direct", "case_id": "c1"},
                {"archive_url": "http://a/z.zip", "acquisition_mode": "manual", "case_id": "c2"},
            ],
        }
        entries = download.iter_download_specs(spec)
        urls = [e["url"] for e in entries]
        self.assertIn("http://a/x.zip", urls)
        self.assertIn("http://a/y.zip", urls)
        self.assertNotIn("http://a/z.zip", urls)  # manual mode excluded
        self.assertEqual(entries[0]["acquisition_mode"], "direct")


class Sha256AndExtractTests(unittest.TestCase):
    def test_sha256_and_zip_extract(self):
        with tempfile.TemporaryDirectory() as t:
            root = Path(t)
            archive = root / "bundle.zip"
            with zipfile.ZipFile(archive, "w") as zf:
                zf.writestr("inner.txt", "hello")
            # sha256 is stable/nonempty
            digest = download.sha256_file(archive)
            self.assertEqual(len(digest), 64)

            ok, dest, mode = download.extract_archive(archive, root / "extracted")
            self.assertTrue(ok)
            self.assertEqual(mode, "zip")
            self.assertTrue((Path(dest) / "inner.txt").exists())

    def test_unsupported_archive(self):
        with tempfile.TemporaryDirectory() as t:
            root = Path(t)
            plain = root / "not_an_archive.bin"
            plain.write_bytes(b"\x00\x01\x02")
            ok, dest, mode = download.extract_archive(plain, root / "out")
            self.assertFalse(ok)
            self.assertEqual(mode, "unsupported_archive")


class NormalizeLegacyTests(unittest.TestCase):
    def test_moves_legacy_into_raw(self):
        with tempfile.TemporaryDirectory() as t:
            target = Path(t)
            raw = target / "raw"
            legacy = target / "file.zip"
            legacy.write_text("x", encoding="utf-8")
            out = download.normalize_legacy_archive_location(target, "file.zip", raw)
            self.assertEqual(out, raw / "file.zip")
            self.assertTrue(out.exists())
            self.assertFalse(legacy.exists())


class CheckRequiredCasesTests(unittest.TestCase):
    def test_flags_unavailable_required_case(self):
        spec = {"cases": [{"case_id": "c1", "acquisition_mode": "manual"}]}
        sr: dict = {"errors": []}
        rep: dict = {"ok": True}
        download.check_required_cases(spec, {"c1"}, sr, rep)
        self.assertFalse(rep["ok"])
        self.assertIn("required_case_unavailable:c1", sr["errors"])

    def test_direct_with_archive_is_ok(self):
        spec = {"cases": [{"case_id": "c1", "acquisition_mode": "direct", "archive_url": "u"}]}
        sr: dict = {"errors": []}
        rep: dict = {"ok": True}
        download.check_required_cases(spec, {"c1"}, sr, rep)
        self.assertTrue(rep["ok"])
        self.assertEqual(sr["errors"], [])


if __name__ == "__main__":
    unittest.main()
