"""Tests for operating-point candidate generation helpers."""
from __future__ import annotations

import unittest
from random import Random

from _bootstrap import REPO_ROOT  # noqa: F401  (ensures src on path)

from grid_data_factory.scenarios import operating_point_generation as opg


class FallbackParsingTests(unittest.TestCase):
    def test_regimes_from_text(self):
        text = "operating_regimes:\n  - baseline\n  - summer_peak\nother_key: 1\n"
        self.assertEqual(opg.fallback_regimes_from_text(text), ["baseline", "summer_peak"])

    def test_regimes_absent(self):
        self.assertEqual(opg.fallback_regimes_from_text("nothing here"), [])

    def test_local_noise(self):
        self.assertEqual(opg.fallback_local_noise("local_noise_stddev: 0.05"), 0.05)
        self.assertEqual(opg.fallback_local_noise("no match", default=0.03), 0.03)


class SampleUnitVectorTests(unittest.TestCase):
    def test_shape_and_bounds(self):
        for sampler in ("sobol", "latin_hypercube", "stratified", "time_series"):
            vec = opg.sample_unit_vector(dim=13, idx=3, total=20, sampler=sampler, rng=Random(1))
            self.assertEqual(len(vec), 13)
            self.assertTrue(all(0.0 <= v <= 1.0 for v in vec))

    def test_sobol_is_deterministic(self):
        a = opg.sample_unit_vector(dim=5, idx=2, total=10, sampler="sobol", rng=Random(1))
        b = opg.sample_unit_vector(dim=5, idx=2, total=10, sampler="sobol", rng=Random(999))
        self.assertEqual(a, b)  # sobol/halton ignores rng


class ChooseRegimeTests(unittest.TestCase):
    def test_regime_specific_cycles(self):
        regimes = ["a", "b", "c"]
        picks = [opg.choose_regime(regimes, i, 6, "regime_specific", Random(0)) for i in range(4)]
        self.assertEqual(picks, ["a", "b", "c", "a"])

    def test_time_series_spans_range(self):
        regimes = ["a", "b", "c"]
        self.assertEqual(opg.choose_regime(regimes, 0, 3, "time_series", Random(0)), "a")
        self.assertEqual(opg.choose_regime(regimes, 2, 3, "time_series", Random(0)), "c")


class BuildCandidateTests(unittest.TestCase):
    def test_shape_and_ranges(self):
        vec = [0.5] * 13
        cand = opg.build_candidate(
            "pglib_opf_case14_ieee", 7, "baseline", vec, 0.0, Random(1), "latin_hypercube",
            grid_family="ieee", dataset="pglib", bus_count=14,
        )
        self.assertEqual(cand["candidate_id"], "pglib_opf_case14_ieee::op::000007")
        self.assertEqual(cand["operating_regime"], "baseline")
        self.assertIn(cand["dc_severity_band"], {"low", "medium", "high"})
        for k in ("novelty_score", "physical_credibility_score", "model_uncertainty_score"):
            self.assertGreaterEqual(cand[k], 0.0)
            self.assertLessEqual(cand[k], 1.0)
        self.assertEqual(cand["contingency_class"], "none")
        self.assertGreater(cand["estimated_compute_cost"], 0.0)

    def test_zero_noise_is_deterministic(self):
        vec = [0.3] * 13
        a = opg.build_candidate("c14", 0, "baseline", vec, 0.0, Random(5), "sobol")
        b = opg.build_candidate("c14", 0, "baseline", vec, 0.0, Random(5), "sobol")
        self.assertEqual(a["operating_point_parameters"], b["operating_point_parameters"])


class BuildSnapshotCandidateTests(unittest.TestCase):
    def test_snapshot_fields(self):
        snap = {"snapshot_id": "s1", "difficulty": "hard", "voltage_regime": "tight", "season": "summer", "total_pd": 42.0}
        topo = {"topology_id": "topology_000000_baseline"}
        cand = opg.build_snapshot_candidate("c14", 2, snap, "ieee", "pglib", 14, topo)
        self.assertEqual(cand["candidate_id"], "c14::snap::000002")
        self.assertEqual(cand["operating_regime"], "seasonal_summer")
        self.assertEqual(cand["load_snapshot_id"], "s1")
        self.assertEqual(cand["snapshot_total_pd"], 42.0)
        self.assertEqual(cand["candidate_generation_mechanism"], "reference_load_snapshot")


if __name__ == "__main__":
    unittest.main()
