"""Tests for resumable-campaign helpers in run_campaign_ac_opf_round.py."""
from __future__ import annotations

import importlib.util
import tempfile
import unittest
from pathlib import Path

from _bootstrap import REPO_ROOT  # noqa: F401  (ensures src on path)

from grid_data_factory.storage.layout import create_next_attempt_directory, finalize_attempt_directory, has_finalized_attempt


def _load_map_module():
    path = REPO_ROOT / "scripts" / "run_campaign_ac_opf_round.py"
    spec = importlib.util.spec_from_file_location("run_campaign_ac_opf_round", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


_MAP = _load_map_module()

_CANDIDATE = {
    "case_id": "pglib_opf_case14_ieee",
    "candidate_id": "pglib_opf_case14_ieee::op::000042",
    "operating_regime": "high renewable",
    "topology_id": "topology_000003_switched",
    "contingency": {
        "event_type": "simultaneous",
        "components": [{"type": "branch", "id": "12"}],
    },
}


class CandidateIdentityTests(unittest.TestCase):
    def test_identity_is_deterministic(self):
        a = _MAP._candidate_identity(_CANDIDATE)
        b = _MAP._candidate_identity(dict(_CANDIDATE))
        self.assertEqual(a, b)
        case_id, topology_id, op_id, ctg_id = a
        self.assertEqual(case_id, "pglib_opf_case14_ieee")
        self.assertEqual(topology_id, "topology_000003_switched")
        self.assertEqual(op_id, "op_000042_high_renewable")
        self.assertTrue(ctg_id.startswith("ctg_k1_b12_"))

    def test_solver_dir_is_stable_and_contains_contingency(self):
        rr = Path("/tmp/rr")
        d1 = _MAP._candidate_solver_dir(rr, _CANDIDATE, "solver_x")
        d2 = _MAP._candidate_solver_dir(rr, dict(_CANDIDATE), "solver_x")
        self.assertEqual(d1, d2)
        self.assertIn("op_000042_high_renewable", str(d1))
        self.assertIn("ctg_k1_b12_", str(d1))
        self.assertTrue(str(d1).endswith("solver_x"))

    def test_resume_skip_detects_finalized_run(self):
        with tempfile.TemporaryDirectory() as tmp:
            rr = Path(tmp)
            solver_dir = _MAP._candidate_solver_dir(rr, _CANDIDATE, "solver_x")
            self.assertFalse(has_finalized_attempt(solver_dir))
            in_progress, _ = create_next_attempt_directory(solver_dir)
            finalize_attempt_directory(in_progress)
            self.assertTrue(has_finalized_attempt(solver_dir))

    def test_distinct_contingencies_map_to_distinct_dirs(self):
        rr = Path("/tmp/rr")
        base = _MAP._candidate_solver_dir(rr, {**_CANDIDATE, "contingency": None}, "solver_x")
        n1 = _MAP._candidate_solver_dir(rr, _CANDIDATE, "solver_x")
        self.assertNotEqual(base, n1)
        self.assertIn("ctg_base", str(base))


if __name__ == "__main__":
    unittest.main()
