"""Connectivity-preservation tests for topology variant generation."""
from __future__ import annotations

import unittest

from _bootstrap import REPO_ROOT, case_available

from grid_data_factory.sources.registry import resolve_case_file
from grid_data_factory.topology.generation import (
    apply_topology,
    generate_topology_variants,
    read_network_skeleton,
)


def _num_components(bus_ids, branches):
    index = {b: i for i, b in enumerate(bus_ids)}
    parent = list(range(len(bus_ids)))

    def find(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    for br in branches:
        a = index.get(br["from"])
        b = index.get(br["to"])
        if a is None or b is None:
            continue
        parent[find(a)] = find(b)
    return len({find(i) for i in range(len(bus_ids))})


class TestTopology(unittest.TestCase):
    def _skeleton(self):
        cid = "epigrids_new_england_250"
        if not case_available(cid):
            self.skipTest(f"{cid} not available")
        bus_ids, branches = read_network_skeleton(resolve_case_file(REPO_ROOT, cid))
        return cid, bus_ids, branches

    def test_baseline_is_first_variant(self):
        cid, bus_ids, branches = self._skeleton()
        variants = generate_topology_variants(cid, bus_ids, branches, n_variants=5, seed=0)
        self.assertGreaterEqual(len(variants), 1)
        self.assertEqual(variants[0]["topology_class"], "baseline")
        self.assertEqual(variants[0].get("switched_off_branches", []), [])

    def test_variants_preserve_connectivity(self):
        cid, bus_ids, branches = self._skeleton()
        base_components = _num_components(bus_ids, branches)
        variants = generate_topology_variants(cid, bus_ids, branches, n_variants=6, seed=0)
        for v in variants:
            out = apply_topology({"branches": branches}, v.get("switched_off_branches"))
            self.assertEqual(_num_components(bus_ids, out["branches"]), base_components)

    def test_variants_are_deterministic(self):
        cid, bus_ids, branches = self._skeleton()
        a = generate_topology_variants(cid, bus_ids, branches, n_variants=6, seed=0)
        b = generate_topology_variants(cid, bus_ids, branches, n_variants=6, seed=0)
        self.assertEqual(
            [v.get("switched_off_branches") for v in a],
            [v.get("switched_off_branches") for v in b],
        )


if __name__ == "__main__":
    unittest.main()
