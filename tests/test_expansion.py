"""Tests for the network-expansion prototype (buses/generators/transformers)."""
from __future__ import annotations

import unittest

from _bootstrap import REPO_ROOT  # noqa: F401  (ensures src on path)

from grid_data_factory.topology.expansion import (
    BUS_SPLIT,
    EXPANSION_CLASSES,
    GENERATOR_ADDITION,
    GREENFIELD_BUS,
    LOAD_ADDITION,
    TRANSFORMER_ADDITION,
    apply_expansion,
    apply_topology_and_expansion,
    generate_expansion_variants,
    is_connected_after_expansion,
)


def _synthetic_case() -> dict:
    """A small connected case with a degree-4 hub (bus 1) to exercise bus_split."""
    buses = [
        {"bus_id": "1", "type": 3, "vm": 1.0, "va": 0.0, "vmin": 0.9, "vmax": 1.1, "gs": 0.0, "bs": 0.0},
        {"bus_id": "2", "type": 1, "vm": 1.0, "va": 0.0, "vmin": 0.9, "vmax": 1.1, "gs": 0.0, "bs": 0.0},
        {"bus_id": "3", "type": 1, "vm": 1.0, "va": 0.0, "vmin": 0.9, "vmax": 1.1, "gs": 0.0, "bs": 0.0},
        {"bus_id": "4", "type": 1, "vm": 1.0, "va": 0.0, "vmin": 0.9, "vmax": 1.1, "gs": 0.0, "bs": 0.0},
        {"bus_id": "5", "type": 2, "vm": 1.0, "va": 0.0, "vmin": 0.9, "vmax": 1.1, "gs": 0.0, "bs": 0.0},
    ]
    generators = [
        {"gen_id": "gen_000001", "bus_id": "1", "pmin": 0.0, "pmax": 200.0, "qmin": -80.0, "qmax": 80.0, "cost": [0.0, 20.0, 0.0]},
        {"gen_id": "gen_000002", "bus_id": "5", "pmin": 0.0, "pmax": 150.0, "qmin": -60.0, "qmax": 60.0, "cost": [0.0, 25.0, 0.0]},
    ]
    loads = [
        {"load_id": "load_000001", "bus_id": "2", "pd": 90.0, "qd": 30.0},
        {"load_id": "load_000002", "bus_id": "3", "pd": 60.0, "qd": 20.0},
        {"load_id": "load_000003", "bus_id": "4", "pd": 70.0, "qd": 25.0},
    ]
    branches = [
        {"branch_id": "branch_000001", "from": "1", "to": "2", "r": 0.01, "x": 0.10, "b": 0.02, "rate_a": 250.0},
        {"branch_id": "branch_000002", "from": "1", "to": "3", "r": 0.02, "x": 0.12, "b": 0.02, "rate_a": 250.0},
        {"branch_id": "branch_000003", "from": "1", "to": "4", "r": 0.02, "x": 0.15, "b": 0.02, "rate_a": 250.0},
        {"branch_id": "branch_000004", "from": "1", "to": "5", "r": 0.01, "x": 0.08, "b": 0.02, "rate_a": 250.0},
        {"branch_id": "branch_000005", "from": "2", "to": "3", "r": 0.03, "x": 0.20, "b": 0.02, "rate_a": 250.0},
        {"branch_id": "branch_000006", "from": "4", "to": "5", "r": 0.03, "x": 0.18, "b": 0.02, "rate_a": 250.0},
    ]
    return {
        "case_id": "synthetic_5bus",
        "base_mva": 100.0,
        "buses": buses,
        "generators": generators,
        "loads": loads,
        "branches": branches,
    }


class TestExpansion(unittest.TestCase):
    def setUp(self):
        self.case = _synthetic_case()
        self.cid = self.case["case_id"]

    def test_baseline_is_first_and_noop(self):
        plans = generate_expansion_variants(self.cid, self.case, n_variants=6, seed=0)
        self.assertEqual(plans[0]["expansion_class"], "baseline")
        self.assertIs(apply_expansion(self.case, plans[0]), self.case)

    def test_deterministic(self):
        a = generate_expansion_variants(self.cid, self.case, n_variants=8, seed=7)
        b = generate_expansion_variants(self.cid, self.case, n_variants=8, seed=7)
        self.assertEqual([p["expansion_id"] for p in a], [p["expansion_id"] for p in b])

    def test_seed_changes_plans(self):
        a = generate_expansion_variants(self.cid, self.case, n_variants=8, seed=1)
        b = generate_expansion_variants(self.cid, self.case, n_variants=8, seed=2)
        self.assertNotEqual([p["expansion_id"] for p in a], [p["expansion_id"] for p in b])

    def test_all_classes_reachable(self):
        plans = generate_expansion_variants(self.cid, self.case, n_variants=12, seed=3)
        classes = {p["expansion_class"] for p in plans}
        for cls in EXPANSION_CLASSES:
            self.assertIn(cls, classes, f"class {cls} not generated")

    def test_generator_addition_adds_a_generator(self):
        plans = generate_expansion_variants(self.cid, self.case, n_variants=12, seed=3)
        plan = next(p for p in plans if p["expansion_class"] == GENERATOR_ADDITION)
        out = apply_expansion(self.case, plan)
        self.assertEqual(len(out["generators"]), len(self.case["generators"]) + 1)
        self.assertEqual(len(out["buses"]), len(self.case["buses"]))  # no new bus
        new_gen = out["generators"][-1]
        self.assertGreater(float(new_gen["pmax"]), 0.0)
        # gen references an existing bus
        self.assertIn(str(new_gen["bus_id"]), {str(b["bus_id"]) for b in out["buses"]})

    def test_transformer_addition_has_offnominal_tap(self):
        plans = generate_expansion_variants(self.cid, self.case, n_variants=12, seed=3)
        plan = next(p for p in plans if p["expansion_class"] == TRANSFORMER_ADDITION)
        out = apply_expansion(self.case, plan)
        self.assertEqual(len(out["branches"]), len(self.case["branches"]) + 1)
        xfmr = out["branches"][-1]
        self.assertTrue(xfmr["transformer"])
        self.assertNotEqual(float(xfmr["tap"]), 1.0)
        # endpoints are existing buses
        bus_set = {str(b["bus_id"]) for b in out["buses"]}
        self.assertIn(str(xfmr["from"]), bus_set)
        self.assertIn(str(xfmr["to"]), bus_set)

    def test_load_addition_adds_a_load(self):
        plans = generate_expansion_variants(self.cid, self.case, n_variants=12, seed=3)
        plan = next(p for p in plans if p["expansion_class"] == LOAD_ADDITION)
        out = apply_expansion(self.case, plan)
        self.assertEqual(len(out["loads"]), len(self.case["loads"]) + 1)
        self.assertEqual(len(out["buses"]), len(self.case["buses"]))  # no new bus
        self.assertEqual(len(out["generators"]), len(self.case["generators"]))
        new_load = out["loads"][-1]
        self.assertGreater(float(new_load["pd"]), 0.0)
        self.assertIn(str(new_load["bus_id"]), {str(b["bus_id"]) for b in out["buses"]})
        self.assertTrue(is_connected_after_expansion(self.case, plan))

    def test_load_addition_scales_with_size_multiplier(self):
        small = generate_expansion_variants(self.cid, self.case, n_variants=12, seed=3, size_multiplier=1.0)
        big = generate_expansion_variants(self.cid, self.case, n_variants=12, seed=3, size_multiplier=4.0)

        def first_new_load_pd(plans):
            plan = next(p for p in plans if p["expansion_class"] == LOAD_ADDITION)
            return float(plan["added_loads"][0]["pd"])

        self.assertAlmostEqual(first_new_load_pd(big), 4.0 * first_new_load_pd(small), places=3)

    def test_greenfield_adds_bus_gen_and_line(self):
        plans = generate_expansion_variants(self.cid, self.case, n_variants=12, seed=3)
        plan = next(p for p in plans if p["expansion_class"] == GREENFIELD_BUS)
        out = apply_expansion(self.case, plan)
        self.assertEqual(len(out["buses"]), len(self.case["buses"]) + 1)
        self.assertEqual(len(out["generators"]), len(self.case["generators"]) + 1)
        self.assertEqual(len(out["branches"]), len(self.case["branches"]) + 1)
        # new bus id is unique
        ids = [str(b["bus_id"]) for b in out["buses"]]
        self.assertEqual(len(ids), len(set(ids)))

    def test_bus_split_reassigns_endpoints_and_adds_tie(self):
        plans = generate_expansion_variants(self.cid, self.case, n_variants=12, seed=3)
        plan = next(p for p in plans if p["expansion_class"] == BUS_SPLIT)
        self.assertGreaterEqual(len(plan["moved_branches"]), 1)
        out = apply_expansion(self.case, plan)
        self.assertEqual(len(out["buses"]), len(self.case["buses"]) + 1)
        # a tie branch was added
        self.assertEqual(len(out["branches"]), len(self.case["branches"]) + 1)
        new_bus_id = plan["new_bus_id"]
        moved_ids = {m["branch_id"] for m in plan["moved_branches"]}
        by_id = {str(b["branch_id"]): b for b in out["branches"]}
        for m in plan["moved_branches"]:
            self.assertEqual(str(by_id[m["branch_id"]][m["endpoint"]]), str(new_bus_id))
        self.assertTrue(moved_ids)

    def test_all_expansions_preserve_connectivity(self):
        plans = generate_expansion_variants(self.cid, self.case, n_variants=12, seed=3)
        for plan in plans:
            self.assertTrue(
                is_connected_after_expansion(self.case, plan),
                f"{plan['expansion_class']} disconnected the network",
            )

    def test_input_case_not_mutated(self):
        before_buses = len(self.case["buses"])
        before_gens = len(self.case["generators"])
        before_branches = len(self.case["branches"])
        plans = generate_expansion_variants(self.cid, self.case, n_variants=12, seed=3)
        for plan in plans:
            apply_expansion(self.case, plan)
        self.assertEqual(len(self.case["buses"]), before_buses)
        self.assertEqual(len(self.case["generators"]), before_gens)
        self.assertEqual(len(self.case["branches"]), before_branches)

    def test_apply_topology_and_expansion_composes(self):
        plans = generate_expansion_variants(self.cid, self.case, n_variants=12, seed=3)
        plan = next(p for p in plans if p["expansion_class"] == GENERATOR_ADDITION)
        # switch off a non-bridge branch and add the new generator in one call
        out = apply_topology_and_expansion(
            self.case,
            switched_off_branches=["branch_000005"],
            reinforced_branches=None,
            expansion=plan,
        )
        branch_ids = {str(b["branch_id"]) for b in out["branches"]}
        self.assertNotIn("branch_000005", branch_ids)
        self.assertEqual(len(out["generators"]), len(self.case["generators"]) + 1)

    def test_default_is_single_step(self):
        plans = generate_expansion_variants(self.cid, self.case, n_variants=12, seed=3)
        for plan in plans:
            if plan["expansion_class"] == "baseline":
                continue
            self.assertEqual(plan.get("step_count", 1), 1)
            self.assertNotIn("steps", plan)

    def test_cascade_adds_multiple_units_sequentially(self):
        plans = generate_expansion_variants(self.cid, self.case, n_variants=16, seed=3, max_steps=3)
        cascades = [p for p in plans if p["expansion_class"] == "cascade_expansion"]
        self.assertTrue(cascades, "no cascade plans produced")
        deepest = max(cascades, key=lambda p: p["step_count"])
        self.assertGreaterEqual(deepest["step_count"], 2)
        self.assertEqual(len(deepest["steps"]), deepest["step_count"])
        # applying the cascade equals applying each step in order
        seq = self.case
        for step in deepest["steps"]:
            seq = apply_expansion(seq, step)
        combined = apply_expansion(self.case, deepest)
        self.assertEqual(len(combined["buses"]), len(seq["buses"]))
        self.assertEqual(len(combined["generators"]), len(seq["generators"]))
        self.assertEqual(len(combined["branches"]), len(seq["branches"]))
        # a multi-step cascade grows the network by more than a single unit
        self.assertGreater(
            len(combined["buses"]) + len(combined["generators"]) + len(combined["branches"]),
            len(self.case["buses"]) + len(self.case["generators"]) + len(self.case["branches"]) + 1,
        )

    def test_cascade_is_deterministic_and_connected(self):
        a = generate_expansion_variants(self.cid, self.case, n_variants=16, seed=5, max_steps=4)
        b = generate_expansion_variants(self.cid, self.case, n_variants=16, seed=5, max_steps=4)
        self.assertEqual([p["expansion_id"] for p in a], [p["expansion_id"] for p in b])
        for plan in a:
            self.assertTrue(is_connected_after_expansion(self.case, plan))

    def test_size_multiplier_scales_capacity(self):
        small = generate_expansion_variants(self.cid, self.case, n_variants=12, seed=3, size_multiplier=1.0)
        big = generate_expansion_variants(self.cid, self.case, n_variants=12, seed=3, size_multiplier=3.0)

        def first_new_gen_pmax(plans):
            plan = next(p for p in plans if p["expansion_class"] == GENERATOR_ADDITION)
            return float(plan["added_generators"][0]["pmax"])

        self.assertAlmostEqual(first_new_gen_pmax(big), 3.0 * first_new_gen_pmax(small), places=3)

    def _big_case(self, n_buses: int) -> dict:
        # Ring of n_buses PQ buses (each bus tied to the next) with a slack + gen
        # at bus 1 and one load, giving a connected, splittable network.
        buses = [
            {"bus_id": str(i), "type": 3 if i == 1 else 1, "vm": 1.0, "va": 0.0,
             "vmin": 0.9, "vmax": 1.1, "gs": 0.0, "bs": 0.0}
            for i in range(1, n_buses + 1)
        ]
        branches = []
        for i in range(1, n_buses + 1):
            j = 1 if i == n_buses else i + 1
            branches.append({"branch_id": f"branch_{i:06d}", "from": str(i), "to": str(j),
                             "r": 0.01, "x": 0.1, "b": 0.02, "rate_a": 250.0})
        # extra chords to raise some degrees >= 4 for bus_split
        for i in range(1, n_buses - 2, 3):
            branches.append({"branch_id": f"chord_{i:06d}", "from": str(i), "to": str(i + 2),
                             "r": 0.02, "x": 0.15, "b": 0.0, "rate_a": 250.0})
        generators = [{"gen_id": "gen_000001", "bus_id": "1", "pmin": 0.0, "pmax": 300.0,
                       "qmin": -100.0, "qmax": 100.0, "cost": [0.0, 20.0, 0.0]}]
        loads = [{"load_id": "load_000001", "bus_id": "2", "pd": 80.0, "qd": 25.0}]
        return {"case_id": f"ring_{n_buses}", "base_mva": 100.0, "buses": buses,
                "generators": generators, "loads": loads, "branches": branches}

    def test_fraction_scales_depth_with_topology_size(self):
        small = self._big_case(20)
        large = self._big_case(400)
        frac = 0.05
        ps = generate_expansion_variants(small["case_id"], small, n_variants=20, seed=1,
                                         max_steps=1000, max_additions_fraction=frac)
        pl = generate_expansion_variants(large["case_id"], large, n_variants=20, seed=1,
                                         max_steps=1000, max_additions_fraction=frac)
        max_depth_small = max(p.get("step_count", 1) for p in ps)
        max_depth_large = max(p.get("step_count", 1) for p in pl)
        # ~5% of 20 buses = 1 unit; ~5% of 400 buses = 20 units -> bigger grid, deeper cascades
        self.assertLessEqual(max_depth_small, 3)
        self.assertGreater(max_depth_large, max_depth_small)
        self.assertGreaterEqual(max_depth_large, 10)

    def test_fraction_floor_and_cap(self):
        tiny = self._big_case(10)
        # tiny grid with a small fraction still yields at least one unit (floor=1)
        plans = generate_expansion_variants(tiny["case_id"], tiny, n_variants=8, seed=1,
                                            max_steps=1000, max_additions_fraction=0.001)
        nonbase = [p for p in plans if p["expansion_class"] != "baseline"]
        self.assertTrue(nonbase)
        self.assertGreaterEqual(min(p.get("step_count", 1) for p in nonbase), 1)
        # max_steps caps the size-derived ceiling
        capped = generate_expansion_variants(self._big_case(400)["case_id"], self._big_case(400),
                                             n_variants=20, seed=1, max_steps=3, max_additions_fraction=0.5)
        self.assertLessEqual(max(p.get("step_count", 1) for p in capped), 3)


if __name__ == "__main__":
    unittest.main()
