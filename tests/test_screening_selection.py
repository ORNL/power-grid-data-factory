"""Tests for the contingency screening selection pipeline."""
from __future__ import annotations

import unittest

from _bootstrap import REPO_ROOT  # noqa: F401  (ensures src on path)

from grid_data_factory.screening import selection


class SeverityBandTests(unittest.TestCase):
    def test_bands(self):
        self.assertEqual(selection.severity_band(0.0), "low")
        self.assertEqual(selection.severity_band(0.24), "low")
        self.assertEqual(selection.severity_band(0.25), "medium")
        self.assertEqual(selection.severity_band(0.59), "medium")
        self.assertEqual(selection.severity_band(0.6), "high")
        self.assertEqual(selection.severity_band(1.0), "high")


class AugmentStrataTests(unittest.TestCase):
    def test_defaults_and_bands(self):
        cand = {"dc_severity_score": 0.7, "voltage_risk_score": 0.3, "reactive_risk_score": 0.1}
        selection.augment_strata(cand)
        self.assertEqual(cand["grid_family"], "unknown")
        self.assertEqual(cand["operating_regime"], "unknown")
        self.assertEqual(cand["contingency_order"], 0)
        self.assertEqual(cand["dc_severity_band"], "high")
        self.assertEqual(cand["voltage_risk_band"], "medium")
        self.assertEqual(cand["reactive_risk_band"], "low")

    def test_does_not_overwrite(self):
        cand = {"grid_family": "ieee", "dc_severity_band": "custom"}
        selection.augment_strata(cand)
        self.assertEqual(cand["grid_family"], "ieee")
        self.assertEqual(cand["dc_severity_band"], "custom")


class AuditSampleTests(unittest.TestCase):
    def test_empty(self):
        self.assertEqual(selection.audit_sample([], 0.1, 0), [])

    def test_target_size_and_determinism(self):
        rejected = [
            {"candidate_id": f"c{i}", "grid_family": "g", "operating_regime": "r", "contingency_order": 0,
             "dc_severity_band": "low", "voltage_risk_band": "low", "reactive_risk_band": "low"}
            for i in range(10)
        ]
        a = selection.audit_sample(rejected, 0.2, seed=7)
        b = selection.audit_sample(rejected, 0.2, seed=7)
        self.assertEqual(len(a), 2)
        self.assertEqual([x["candidate_id"] for x in a], [x["candidate_id"] for x in b])

    def test_minimum_one(self):
        rejected = [{"candidate_id": "c0"}]
        self.assertEqual(len(selection.audit_sample(rejected, 0.0, seed=0)), 1)


class ScreenCandidatesTests(unittest.TestCase):
    def test_accept_reject_and_audit_tag(self):
        thresholds = {"dc_severity": 0.5}
        candidates = [
            {"candidate_id": "hi", "dc_severity_score": 0.9},
            {"candidate_id": "lo", "dc_severity_score": 0.1},
        ]
        result = selection.screen_candidates(candidates, thresholds, audit_fraction=1.0, seed=0)

        self.assertEqual(len(result["accepted"]), 1)
        self.assertEqual(result["accepted"][0]["candidate_id"], "hi")
        self.assertEqual(len(result["rejected"]), 1)

        # audit_fraction=1.0 pulls the rejected candidate into the AC selection.
        selected_ids = {c["candidate_id"] for c in result["selected_for_ac"]}
        self.assertEqual(selected_ids, {"hi", "lo"})

        lo = next(c for c in result["selected_for_ac"] if c["candidate_id"] == "lo")
        self.assertTrue(lo["required_audit_sample"])
        self.assertIn("audit_rejected_region", lo["screening_reasons"])

    def test_progress_callback(self):
        calls: list[tuple[int, int, int]] = []
        candidates = [{"candidate_id": str(i), "dc_severity_score": 1.0} for i in range(4)]
        selection.screen_candidates(
            candidates,
            {"dc_severity": 0.5},
            audit_fraction=0.0,
            seed=0,
            progress_every=2,
            on_progress=lambda done, total, acc: calls.append((done, total, acc)),
        )
        self.assertEqual(calls, [(2, 4, 2), (4, 4, 4)])


if __name__ == "__main__":
    unittest.main()
