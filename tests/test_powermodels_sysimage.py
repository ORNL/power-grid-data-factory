import os
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from _bootstrap import REPO_ROOT  # noqa: F401  (ensures src on path)

from grid_data_factory.solvers.powermodels_adapter import PowerModelsAdapter


class SysimageResolutionTests(unittest.TestCase):
    def setUp(self) -> None:
        self._saved = os.environ.get("PGDF_JULIA_SYSIMAGE")
        os.environ.pop("PGDF_JULIA_SYSIMAGE", None)

    def tearDown(self) -> None:
        if self._saved is None:
            os.environ.pop("PGDF_JULIA_SYSIMAGE", None)
        else:
            os.environ["PGDF_JULIA_SYSIMAGE"] = self._saved

    def test_no_sysimage_uses_compiled_modules_no(self) -> None:
        with TemporaryDirectory() as tmp:
            adapter = PowerModelsAdapter(repo_root=Path(tmp))
            self.assertIsNone(adapter.sysimage_path)
            self.assertEqual(adapter._julia_mode_flags(), ["--compiled-modules=no"])

    def test_env_override_used_when_file_exists(self) -> None:
        with TemporaryDirectory() as tmp:
            so = Path(tmp) / "custom.so"
            so.write_bytes(b"stub")
            os.environ["PGDF_JULIA_SYSIMAGE"] = str(so)
            adapter = PowerModelsAdapter(repo_root=Path(tmp))
            self.assertEqual(adapter.sysimage_path, so)
            self.assertEqual(adapter._julia_mode_flags(), [f"--sysimage={so}"])

    def test_env_override_ignored_when_missing(self) -> None:
        with TemporaryDirectory() as tmp:
            os.environ["PGDF_JULIA_SYSIMAGE"] = str(Path(tmp) / "does_not_exist.so")
            adapter = PowerModelsAdapter(repo_root=Path(tmp))
            self.assertIsNone(adapter.sysimage_path)
            self.assertEqual(adapter._julia_mode_flags(), ["--compiled-modules=no"])

    def test_local_platform_sysimage_autodetected(self) -> None:
        with TemporaryDirectory() as tmp:
            so = Path(tmp) / "julia" / "sysimages" / "local" / "pgdf_sysimage.so"
            so.parent.mkdir(parents=True, exist_ok=True)
            so.write_bytes(b"stub")
            resolved = PowerModelsAdapter.resolve_julia_sysimage(Path(tmp))
            self.assertEqual(resolved, so)

    def test_no_repo_root_returns_none(self) -> None:
        self.assertIsNone(PowerModelsAdapter.resolve_julia_sysimage(None))


if __name__ == "__main__":
    unittest.main()
