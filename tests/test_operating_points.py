"""Tests for operating-point transforms (scaling + snapshot rebuild)."""
from __future__ import annotations

import unittest

from _bootstrap import REPO_ROOT, case_available

from grid_data_factory.parsers.matpower import parse_matpower_case
from grid_data_factory.scenarios.operating_points import apply_operating_point, region_for_bus
from grid_data_factory.sources.registry import resolve_case_file

_BASE = {
    "case_id": "toy",
    "base_mva": 100.0,
    "buses": [{"bus_id": "1", "type": 3}, {"bus_id": "2", "type": 1}],
    "generators": [{"gen_id": "gen_000001", "bus_id": "1", "pmin": 0.0, "pmax": 100.0, "qmin": -50.0, "qmax": 50.0, "cost": [0.0, 10.0, 0.0]}],
    "loads": [{"load_id": "load_000001", "bus_id": "2", "pd": 50.0, "qd": 20.0}],
    "branches": [{"branch_id": "branch_000001", "from": "1", "to": "2", "r": 0.01, "x": 0.1, "rate_a": 100.0}],
}


class TestOperatingPoints(unittest.TestCase):
    def test_region_partition(self):
        self.assertEqual({region_for_bus(str(i)) for i in range(4)}, {"north", "south", "east", "west"})

    def test_global_load_scale(self):
        out = apply_operating_point(_BASE, {"global_load_scale": 2.0})
        self.assertAlmostEqual(out["loads"][0]["pd"], 100.0)
        # base must be untouched
        self.assertAlmostEqual(_BASE["loads"][0]["pd"], 50.0)

    def test_branch_rating_scale(self):
        out = apply_operating_point(_BASE, {"branch_rating_scale": 0.5})
        self.assertAlmostEqual(out["branches"][0]["rate_a"], 50.0)

    def test_transformer_sweep_off_by_default(self):
        xfmr_case = {
            **_BASE,
            "branches": [
                {"branch_id": "line_1", "from": "1", "to": "2", "r": 0.01, "x": 0.1, "rate_a": 100.0, "tap": 1.0, "shift": 0.0},
                {"branch_id": "xfmr_1", "from": "1", "to": "2", "r": 0.01, "x": 0.1, "rate_a": 100.0, "tap": 1.02, "shift": 3.0, "transformer": True},
            ],
        }
        out = apply_operating_point(xfmr_case, {"perturbation_seed": 7})
        self.assertEqual(out["branches"][0]["tap"], 1.0)
        self.assertEqual(out["branches"][0]["shift"], 0.0)
        self.assertEqual(out["branches"][1]["tap"], 1.02)
        self.assertEqual(out["branches"][1]["shift"], 3.0)

    def test_transformer_sweep_only_affects_transformers(self):
        xfmr_case = {
            **_BASE,
            "branches": [
                {"branch_id": "line_1", "from": "1", "to": "2", "r": 0.01, "x": 0.1, "rate_a": 100.0, "tap": 1.0, "shift": 0.0},
                {"branch_id": "xfmr_1", "from": "1", "to": "2", "r": 0.01, "x": 0.1, "rate_a": 100.0, "tap": 1.02, "shift": 3.0, "transformer": True},
            ],
        }
        params = {"perturbation_seed": 7, "transformer_tap_sigma": 0.05, "transformer_shift_sigma": 2.0}
        out = apply_operating_point(xfmr_case, params)
        # Plain line (tap=1, shift=0, not a transformer) is left untouched.
        self.assertEqual(out["branches"][0]["tap"], 1.0)
        self.assertEqual(out["branches"][0]["shift"], 0.0)
        # Transformer tap/shift move, and stay within the clamp band.
        self.assertNotAlmostEqual(out["branches"][1]["tap"], 1.02)
        self.assertNotAlmostEqual(out["branches"][1]["shift"], 3.0)
        self.assertGreaterEqual(out["branches"][1]["tap"], 0.9)
        self.assertLessEqual(out["branches"][1]["tap"], 1.1)

    def test_transformer_sweep_is_deterministic(self):
        xfmr_case = {
            **_BASE,
            "branches": [
                {"branch_id": "xfmr_1", "from": "1", "to": "2", "r": 0.01, "x": 0.1, "rate_a": 100.0, "tap": 1.02, "shift": 3.0, "transformer": True},
            ],
        }
        params = {"perturbation_seed": 11, "transformer_tap_sigma": 0.05, "transformer_shift_sigma": 2.0}
        a = apply_operating_point(xfmr_case, params)
        b = apply_operating_point(xfmr_case, params)
        self.assertEqual(a["branches"][0]["tap"], b["branches"][0]["tap"])
        self.assertEqual(a["branches"][0]["shift"], b["branches"][0]["shift"])

    def test_snapshot_rebuild(self):
        snap = {"2": [123.0, 45.0], "1": [0.0, 0.0]}
        out = apply_operating_point(_BASE, {"_load_snapshot_map": snap})
        self.assertEqual(len(out["loads"]), 1)
        self.assertAlmostEqual(out["loads"][0]["pd"], 123.0)
        self.assertEqual(out["loads"][0]["bus_id"], "2")

    def test_snapshot_roundtrip_new_england(self):
        cid = "epigrids_new_england_250"
        if not case_available(cid):
            self.skipTest(f"{cid} not available")
        from grid_data_factory.scenarios.load_snapshots import get_snapshot_bus_loads, load_snapshot_registry

        reg = load_snapshot_registry(REPO_ROOT, cid)
        if not reg or not reg.get("snapshots"):
            self.skipTest("no snapshot registry")
        base = parse_matpower_case(resolve_case_file(REPO_ROOT, cid), cid)
        for snap in reg["snapshots"].values():
            sid = snap["snapshot_id"]
            loads = get_snapshot_bus_loads(REPO_ROOT, cid, sid)
            out = apply_operating_point(base, {"_load_snapshot_map": loads})
            total = sum(l["pd"] for l in out["loads"])
            self.assertAlmostEqual(total, snap["total_pd"], places=2)


if __name__ == "__main__":
    unittest.main()
