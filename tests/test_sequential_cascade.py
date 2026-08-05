"""Tests for sequential-cascade contingency enumeration and application."""
from __future__ import annotations

import unittest
from random import Random
from types import SimpleNamespace

from _bootstrap import REPO_ROOT  # noqa: F401  (ensures src on path)

from grid_data_factory.contingencies.apply import apply_contingency, contingency_slug
from grid_data_factory.contingencies.enumeration import expand_one


def _sampling(**overrides):
    base = {
        "seed": 1,
        "n1_per_operating_point": 0,
        "n2_random_per_operating_point": 0,
        "n2_interacting_per_operating_point": 0,
        "n1n1_per_operating_point": 0,
        "max_k": 10,
        "nk_per_operating_point": 0,
        "sequential_cascade_per_operating_point": 0,
        "sequential_max_len": 10,
    }
    base.update(overrides)
    return SimpleNamespace(**base)


_BASE_ROW = {
    "case_id": "pglib_opf_case118_ieee",
    "candidate_id": "op_0001",
    "contingency_severity_score": 0.2,
    "physical_credibility_score": 0.9,
}


class SequentialCascadeEnumerationTests(unittest.TestCase):
    def test_disabled_by_default_emits_no_cascades(self):
        rng = Random(0)
        rows = expand_one(_BASE_ROW, rng, _sampling(), repo_root=None)
        self.assertEqual(rows, [])

    def test_generates_cascades_depth_3_to_max(self):
        rng = Random(0)
        rows = expand_one(
            _BASE_ROW,
            rng,
            _sampling(sequential_cascade_per_operating_point=2, sequential_max_len=6),
            repo_root=None,
        )
        cascades = [r for r in rows if r["contingency"]["event_type"] == "sequential_cascade"]
        self.assertTrue(cascades)
        depths = {r["contingency"]["order"] for r in cascades}
        # Depths 3..6, two per depth.
        self.assertEqual(depths, {3, 4, 5, 6})
        self.assertEqual(len(cascades), 2 * 4)

    def test_cascade_schema_supports_both_semantics(self):
        rng = Random(0)
        rows = expand_one(
            _BASE_ROW,
            rng,
            _sampling(sequential_cascade_per_operating_point=1, sequential_max_len=4),
            repo_root=None,
        )
        cont = next(r["contingency"] for r in rows if r["contingency"]["event_type"] == "sequential_cascade")
        stages = cont["stages"]
        # Ordered, contiguous stage indices; final stage has no corrective action.
        self.assertEqual([s["index"] for s in stages], list(range(1, len(stages) + 1)))
        self.assertNotIn("corrective_action", stages[-1])
        self.assertIn("corrective_action", stages[0])
        # Endogenous seed knob present for the physics-driven path.
        self.assertEqual(cont["seed_stage_count"], 1)
        self.assertIn("cascade_induced", cont["ontology_labels"])

    def test_max_len_capped_at_max_k(self):
        rng = Random(0)
        rows = expand_one(
            _BASE_ROW,
            rng,
            _sampling(sequential_cascade_per_operating_point=1, sequential_max_len=10, max_k=4),
            repo_root=None,
        )
        depths = {r["contingency"]["order"] for r in rows if r["contingency"]["event_type"] == "sequential_cascade"}
        self.assertEqual(max(depths), 4)


class SequentialCascadeApplyTests(unittest.TestCase):
    _CASE = {
        "generators": [{"gen_id": "gen_000001"}, {"gen_id": "gen_000002"}],
        "branches": [{"branch_id": f"branch_{i:06d}"} for i in range(1, 6)],
    }

    def test_apply_removes_all_staged_components(self):
        cont = {
            "event_type": "sequential_cascade",
            "stages": [
                {"index": 1, "type": "branch", "id": "branch_000001"},
                {"index": 2, "type": "branch", "id": "branch_000003"},
                {"index": 3, "type": "generator", "id": "gen_000002"},
            ],
        }
        out = apply_contingency(self._CASE, cont)
        self.assertEqual(len(out["branches"]), 3)
        self.assertEqual(len(out["generators"]), 1)
        # Input untouched.
        self.assertEqual(len(self._CASE["branches"]), 5)

    def test_slug_marks_cascade_as_sequential(self):
        cont = {
            "event_type": "sequential_cascade",
            "stages": [
                {"index": 1, "type": "branch", "id": "branch_000001"},
                {"index": 2, "type": "generator", "id": "gen_000002"},
            ],
        }
        slug = contingency_slug(cont)
        self.assertTrue(slug.startswith("ctg_seq2_"))


if __name__ == "__main__":
    unittest.main()
