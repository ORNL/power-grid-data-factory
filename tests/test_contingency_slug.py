"""Tests for the contingency directory-name slug."""
from __future__ import annotations

import unittest

from _bootstrap import REPO_ROOT  # noqa: F401  (ensures src on path)

from grid_data_factory.contingencies.apply import contingency_slug


class ContingencySlugTests(unittest.TestCase):
    def test_base_case(self):
        self.assertEqual(contingency_slug(None), "ctg_base")
        self.assertEqual(contingency_slug({}), "ctg_base")

    def test_n1_branch_is_readable(self):
        slug = contingency_slug(
            {"event_type": "simultaneous", "components": [{"type": "branch", "id": "12"}]}
        )
        self.assertTrue(slug.startswith("ctg_k1_b12_"))

    def test_n2_mixed_components(self):
        slug = contingency_slug(
            {
                "event_type": "simultaneous",
                "components": [{"type": "branch", "id": "12"}, {"type": "generator", "id": "3"}],
            }
        )
        self.assertTrue(slug.startswith("ctg_k2_"))
        self.assertIn("b12", slug)
        self.assertIn("g3", slug)

    def test_sequential_preserves_order_tag(self):
        slug = contingency_slug(
            {
                "event_type": "sequential_n1n1",
                "first_outage": {"type": "branch", "id": "5"},
                "second_outage": {"type": "branch", "id": "9"},
            }
        )
        self.assertTrue(slug.startswith("ctg_seq2_b5-b9_"))

    def test_simultaneous_is_order_independent(self):
        a = contingency_slug(
            {"event_type": "simultaneous", "components": [{"type": "branch", "id": "12"}, {"type": "branch", "id": "34"}]}
        )
        b = contingency_slug(
            {"event_type": "simultaneous", "components": [{"type": "branch", "id": "34"}, {"type": "branch", "id": "12"}]}
        )
        self.assertEqual(a, b)

    def test_distinct_contingencies_differ(self):
        a = contingency_slug({"event_type": "simultaneous", "components": [{"type": "branch", "id": "12"}]})
        b = contingency_slug({"event_type": "simultaneous", "components": [{"type": "branch", "id": "13"}]})
        self.assertNotEqual(a, b)

    def test_slug_is_filesystem_safe(self):
        slug = contingency_slug(
            {"event_type": "simultaneous", "components": [{"type": "branch", "id": "bus/1->bus 2"}]}
        )
        self.assertNotIn("/", slug)
        self.assertNotIn(" ", slug)


if __name__ == "__main__":
    unittest.main()
