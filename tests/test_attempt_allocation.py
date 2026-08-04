"""Tests for collision-safe attempt-directory allocation under concurrency."""
from __future__ import annotations

import concurrent.futures
import tempfile
import unittest
from pathlib import Path

from _bootstrap import REPO_ROOT  # noqa: F401  (ensures src on path)

from grid_data_factory.storage.layout import (
    create_next_attempt_directory,
    finalize_attempt_directory,
    has_finalized_attempt,
    scan_max_attempt_index,
)


def _allocate_and_finalize(solver_dir_str: str) -> str:
    in_progress, attempt_id = create_next_attempt_directory(Path(solver_dir_str))
    finalize_attempt_directory(in_progress)
    return attempt_id


class AttemptAllocationTests(unittest.TestCase):
    def test_sequential_indices_are_distinct_and_increasing(self):
        with tempfile.TemporaryDirectory() as tmp:
            solver_dir = Path(tmp) / "solver"
            ids = []
            for _ in range(5):
                in_progress, attempt_id = create_next_attempt_directory(solver_dir)
                finalize_attempt_directory(in_progress)
                ids.append(attempt_id)
            self.assertEqual(ids, [f"attempt_{i:06d}" for i in range(1, 6)])
            self.assertEqual(scan_max_attempt_index(solver_dir), 5)

    def test_concurrent_allocation_has_no_collisions(self):
        # Threads sharing one solver dir must each get a unique attempt directory.
        with tempfile.TemporaryDirectory() as tmp:
            solver_dir = str(Path(tmp) / "solver")
            n = 64
            with concurrent.futures.ThreadPoolExecutor(max_workers=16) as pool:
                ids = list(pool.map(lambda _: _allocate_and_finalize(solver_dir), range(n)))
            self.assertEqual(len(ids), n)
            self.assertEqual(len(set(ids)), n)
            finalized = list((Path(solver_dir) / "attempts").glob("attempt_*"))
            self.assertEqual(len(finalized), n)

    def test_allocator_skips_preexisting_finalized_dirs(self):
        with tempfile.TemporaryDirectory() as tmp:
            solver_dir = Path(tmp) / "solver"
            (solver_dir / "attempts" / "attempt_000001").mkdir(parents=True)
            (solver_dir / "attempts" / "attempt_000002").mkdir(parents=True)
            _, attempt_id = create_next_attempt_directory(solver_dir)
            self.assertEqual(attempt_id, "attempt_000003")


class HasFinalizedAttemptTests(unittest.TestCase):
    def test_false_when_no_attempts_dir(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.assertFalse(has_finalized_attempt(Path(tmp) / "solver"))

    def test_false_when_only_in_progress(self):
        with tempfile.TemporaryDirectory() as tmp:
            solver_dir = Path(tmp) / "solver"
            create_next_attempt_directory(solver_dir)  # leaves an .in_progress marker
            self.assertFalse(has_finalized_attempt(solver_dir))

    def test_true_after_finalize(self):
        with tempfile.TemporaryDirectory() as tmp:
            solver_dir = Path(tmp) / "solver"
            in_progress, _ = create_next_attempt_directory(solver_dir)
            finalize_attempt_directory(in_progress)
            self.assertTrue(has_finalized_attempt(solver_dir))


if __name__ == "__main__":
    unittest.main()
