"""Tests for contingency application (component outages)."""
from __future__ import annotations

import unittest

from _bootstrap import REPO_ROOT  # noqa: F401  (ensures src on path)

from grid_data_factory.contingencies.apply import apply_contingency, remove_component

_BASE = {
    "generators": [{"gen_id": "gen_000001"}, {"gen_id": "gen_000002"}],
    "branches": [{"branch_id": "branch_000001"}, {"branch_id": "branch_000002"}, {"branch_id": "branch_000003"}],
}


class TestContingencies(unittest.TestCase):
    def test_none_is_noop(self):
        self.assertIs(apply_contingency(_BASE, None), _BASE)

    def test_simultaneous_removes_components(self):
        cont = {"event_type": "simultaneous", "components": [{"type": "branch", "id": "branch_000002"}]}
        out = apply_contingency(_BASE, cont)
        self.assertEqual(len(out["branches"]), 2)
        self.assertEqual(len(_BASE["branches"]), 3)  # base untouched

    def test_sequential_n1n1_removes_two(self):
        cont = {
            "event_type": "sequential_n1n1",
            "first_outage": {"type": "branch", "id": "branch_000001"},
            "second_outage": {"type": "generator", "id": "gen_000002"},
        }
        out = apply_contingency(_BASE, cont)
        self.assertEqual(len(out["branches"]), 2)
        self.assertEqual(len(out["generators"]), 1)

    def test_remove_component_unknown_type_noop(self):
        d = {"branches": list(_BASE["branches"]), "generators": list(_BASE["generators"])}
        remove_component(d, "bus", "1")
        self.assertEqual(len(d["branches"]), 3)


if __name__ == "__main__":
    unittest.main()
