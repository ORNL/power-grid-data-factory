"""Tests for the enumeration-time feasibility prefilter."""
from __future__ import annotations

import unittest
from random import Random
from types import SimpleNamespace

from _bootstrap import REPO_ROOT

from grid_data_factory.contingencies import feasibility as F
from grid_data_factory.contingencies.enumeration import expand_one


def _cycle_ctx() -> F.CaseContext:
    # 4-node cycle: any single branch removal stays connected; removing two
    # opposite branches splits the graph into two components.
    return F.CaseContext(
        case_id="toy",
        bus_count=4,
        edges=[(0, 1), (1, 2), (2, 3), (3, 0)],
        branch_edge_index={
            "branch_000001": 0,
            "branch_000002": 1,
            "branch_000003": 2,
            "branch_000004": 3,
        },
        base_components=1,
        total_pd=100.0,
        gen_list=[60.0, 60.0],
        gen_id_to_pos={"gen_000001": 0, "gen_000002": 1},
    )


class OrderCapTests(unittest.TestCase):
    def test_small_case_allows_n1_only(self):
        self.assertTrue(F.order_allowed(14, 1, "simultaneous"))
        self.assertFalse(F.order_allowed(14, 2, "simultaneous"))
        self.assertFalse(F.order_allowed(14, 3, "simultaneous"))

    def test_medium_case_allows_up_to_n2(self):
        self.assertTrue(F.order_allowed(57, 2, "simultaneous"))
        self.assertTrue(F.order_allowed(57, 2, "sequential_n1n1"))
        self.assertFalse(F.order_allowed(57, 3, "simultaneous"))
        self.assertFalse(F.order_allowed(57, 3, "sequential_cascade"))

    def test_large_case_allows_high_order(self):
        self.assertTrue(F.order_allowed(118, 5, "simultaneous"))
        self.assertTrue(F.order_allowed(118, 6, "sequential_cascade"))

    def test_unknown_size_does_not_restrict(self):
        self.assertTrue(F.order_allowed(0, 8, "simultaneous"))


class ConnectivityTests(unittest.TestCase):
    def test_single_removal_on_cycle_stays_connected(self):
        ctx = _cycle_ctx()
        cont = {"event_type": "simultaneous", "components": [{"type": "branch", "id": "branch_000001"}]}
        self.assertFalse(F.creates_island(ctx, [], cont))

    def test_opposite_removals_create_island(self):
        ctx = _cycle_ctx()
        cont = {
            "event_type": "simultaneous",
            "components": [
                {"type": "branch", "id": "branch_000001"},
                {"type": "branch", "id": "branch_000003"},
            ],
        }
        self.assertTrue(F.creates_island(ctx, [], cont))

    def test_switched_plus_contingency_combine(self):
        ctx = _cycle_ctx()
        cont = {"event_type": "simultaneous", "components": [{"type": "branch", "id": "branch_000003"}]}
        # switched_off branch_000001 + contingency branch_000003 = two opposite cuts.
        self.assertTrue(F.creates_island(ctx, ["branch_000001"], cont))

    def test_generator_only_contingency_never_islands(self):
        ctx = _cycle_ctx()
        cont = {"event_type": "simultaneous", "components": [{"type": "generator", "id": "gen_000001"}]}
        self.assertFalse(F.creates_island(ctx, [], cont))

    def test_unknown_branch_ids_are_noops(self):
        ctx = _cycle_ctx()
        cont = {"event_type": "simultaneous", "components": [{"type": "branch", "id": "branch_009999"}]}
        self.assertFalse(F.creates_island(ctx, [], cont))


class AdequacyTests(unittest.TestCase):
    def test_adequate_at_nominal_load(self):
        ctx = _cycle_ctx()  # 120 MW capacity vs 100 MW load
        self.assertTrue(F.generation_adequate(ctx, {"global_load_scale": 1.0}, None))

    def test_inadequate_when_load_exceeds_capacity(self):
        ctx = _cycle_ctx()
        self.assertFalse(F.generation_adequate(ctx, {"global_load_scale": 1.2}, None))

    def test_inadequate_when_fleet_derated(self):
        ctx = _cycle_ctx()
        self.assertFalse(
            F.generation_adequate(ctx, {"global_load_scale": 1.0, "generator_fleet_availability": 0.5}, None)
        )

    def test_generator_contingency_reduces_capacity(self):
        ctx = _cycle_ctx()
        cont = {"event_type": "simultaneous", "components": [{"type": "generator", "id": "gen_000002"}]}
        # Removing one 60 MW unit leaves 60 MW < 100 MW load.
        self.assertFalse(F.generation_adequate(ctx, {"global_load_scale": 1.0}, cont))


class ExpandOneIntegrationTests(unittest.TestCase):
    def _sampling(self, **overrides):
        base = {
            "seed": 1,
            "n1_per_operating_point": 3,
            "n2_random_per_operating_point": 2,
            "n2_interacting_per_operating_point": 2,
            "n1n1_per_operating_point": 1,
            "max_k": 5,
            "nk_per_operating_point": 1,
            "sequential_cascade_per_operating_point": 1,
            "sequential_max_len": 5,
        }
        base.update(overrides)
        return SimpleNamespace(**base)

    def test_small_case_keeps_only_n1(self):
        base = {"case_id": "pglib_opf_case14_ieee", "candidate_id": "op1", "physical_credibility_score": 0.9}
        stats: dict[str, int] = {}
        rows = expand_one(base, Random(1), self._sampling(), repo_root=REPO_ROOT, stats=stats)
        for row in rows:
            self.assertEqual(row["contingency"]["order"], 1)
            self.assertEqual(row["contingency"]["event_type"], "simultaneous")
        self.assertGreater(stats.get("dropped_order", 0), 0)

    def test_inadequate_operating_point_dropped_entirely(self):
        base = {
            "case_id": "pglib_opf_case14_ieee",
            "candidate_id": "op2",
            "physical_credibility_score": 0.9,
            "operating_point_parameters": {"global_load_scale": 5.0},
        }
        stats: dict[str, int] = {}
        rows = expand_one(base, Random(1), self._sampling(), repo_root=REPO_ROOT, stats=stats)
        self.assertEqual(rows, [])
        self.assertEqual(stats.get("dropped_op_inadequate", 0), 1)

    def test_prefilter_off_preserves_high_order(self):
        base = {"case_id": "pglib_opf_case14_ieee", "candidate_id": "op3", "physical_credibility_score": 0.9}
        rows = expand_one(base, Random(1), self._sampling(feasibility_prefilter=False), repo_root=REPO_ROOT)
        orders = {row["contingency"]["order"] for row in rows}
        self.assertTrue(max(orders) >= 3)

    def test_fail_open_on_unresolvable_case(self):
        base = {"case_id": "not_a_real_case", "candidate_id": "op4", "physical_credibility_score": 0.9}
        # No case file resolves -> network filters no-op, order caps use the
        # pool hint; enumeration must not raise and must still emit rows.
        rows = expand_one(base, Random(1), self._sampling(), repo_root=REPO_ROOT)
        self.assertTrue(rows)


if __name__ == "__main__":
    unittest.main()
