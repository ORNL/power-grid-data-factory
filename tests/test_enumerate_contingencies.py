"""Regression tests for high-order contingency enumeration (K>=3)."""
from __future__ import annotations

import importlib.util
import unittest
from random import Random

from _bootstrap import REPO_ROOT  # noqa: F401  (ensures src on path)


def _load_enumerate_module():
    path = REPO_ROOT / "scripts" / "enumerate_contingencies.py"
    spec = importlib.util.spec_from_file_location("enumerate_contingencies", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


_ENUM = _load_enumerate_module()


class BuildKPlusComponentsTests(unittest.TestCase):
    def test_terminates_and_is_distinct_with_few_generators(self):
        # Regression: the old builder looped forever when generators were scarce and K>2.
        pool = {
            "branch": [f"branch_{i:06d}" for i in range(1, 22)],
            "generator": ["gen_000001", "gen_000002"],
            "bus": [f"bus_{i:06d}" for i in range(1, 15)],
        }
        for k in range(3, 11):
            comps = _ENUM._build_kplus_components(pool, k, Random(k))
            self.assertEqual(len(comps), k)
            keys = {(c["type"], c["id"]) for c in comps}
            self.assertEqual(len(keys), k)
            self.assertTrue(any(c["type"] == "branch" for c in comps))

    def test_k_exceeding_pool_returns_all_distinct(self):
        pool = {"branch": ["branch_000001", "branch_000002"], "generator": ["gen_000001"], "bus": ["bus_000001"]}
        comps = _ENUM._build_kplus_components(pool, 10, Random(1))
        self.assertEqual(len(comps), 3)
        keys = {(c["type"], c["id"]) for c in comps}
        self.assertEqual(len(keys), 3)

    def test_expand_one_reaches_requested_order(self):
        class _Args:
            n1_per_operating_point = 3
            n2_random_per_operating_point = 2
            n2_interacting_per_operating_point = 2
            n1n1_per_operating_point = 1
            max_k = 10
            nk_per_operating_point = 1

        base = {"case_id": "pglib_opf_case14_ieee", "candidate_id": "op1", "physical_credibility_score": 0.9}
        rows = _ENUM._expand_one(base, Random(1), _Args())
        orders = {r["contingency"]["order"] for r in rows}
        self.assertEqual(max(orders), 10)
        self.assertTrue({1, 2}.issubset(orders))


if __name__ == "__main__":
    unittest.main()
