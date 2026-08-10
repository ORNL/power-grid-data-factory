import os
import unittest
from io import StringIO
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from _bootstrap import REPO_ROOT  # noqa: F401  (ensures src on path)

from grid_data_factory.solvers.powermodels_adapter import PersistentPowerModelsSession, PowerModelsAdapter


class _FakeProcess:
    def __init__(self) -> None:
        self.stdin = StringIO()
        self.stdout = StringIO(
            'PowerModels informational output\n'
            'PGDF_RESULT\t{"success": true, "termination_status": "LOCALLY_SOLVED"}\n'
            'PGDF_RESULT\t{"success": false, "termination_status": "LOCALLY_INFEASIBLE"}\n'
        )
        self.pid = 1234
        self.returncode = None

    def poll(self):
        return self.returncode

    def wait(self, timeout=None):
        self.returncode = 0
        return 0

    def terminate(self):
        self.returncode = -15

    def kill(self):
        self.returncode = -9


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


class PersistentSessionTests(unittest.TestCase):
    def test_reuses_process_and_ignores_unframed_stdout(self) -> None:
        with TemporaryDirectory() as tmp:
            adapter = PowerModelsAdapter(repo_root=Path(tmp))
            session = PersistentPowerModelsSession(adapter, {"timeout_s": 5})
            process = _FakeProcess()

            with patch.object(session, "_start", side_effect=lambda: setattr(session, "process", process)) as start:
                with patch("grid_data_factory.solvers.powermodels_adapter.select.select", side_effect=lambda streams, *_: (streams, [], [])):
                    first = session.solve_ac_opf({"case_id": "case"})
                    second = session.solve_ac_opf({"case_id": "case"})

            self.assertEqual(start.call_count, 1)
            self.assertTrue(first["success"])
            self.assertEqual(second["termination_status"], "LOCALLY_INFEASIBLE")
            self.assertTrue(first["runtime_metadata"]["persistent_julia"])
            self.assertEqual(process.stdin.getvalue().count("\n"), 2)


if __name__ == "__main__":
    unittest.main()
