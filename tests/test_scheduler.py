"""Tests for the campaign budget planner and multi-round global scheduler."""
from __future__ import annotations

import unittest

import _bootstrap  # noqa: F401  (path setup side effect)

from grid_data_factory.campaigns.planning import plan_round_budgets, round_weights
from grid_data_factory.campaigns.scheduler import (
    GlobalScheduler,
    RoundMetrics,
    StoppingConfig,
    evaluate_stopping,
    plan_rounds,
)


class BudgetPlannerTests(unittest.TestCase):
    def test_constant_sums_to_total_and_is_even(self):
        budgets = plan_round_budgets(1000, 4, schedule="constant")
        self.assertEqual(sum(budgets), 1000)
        self.assertEqual(budgets, [250, 250, 250, 250])

    def test_remainder_distributed_to_early_rounds(self):
        budgets = plan_round_budgets(10, 3, schedule="constant")
        self.assertEqual(sum(budgets), 10)
        self.assertEqual(budgets, [4, 3, 3])

    def test_linear_growth_is_monotonic(self):
        budgets = plan_round_budgets(1000, 4, schedule="linear", ratio=1.0)
        self.assertEqual(sum(budgets), 1000)
        self.assertTrue(all(budgets[i] <= budgets[i + 1] for i in range(len(budgets) - 1)))

    def test_geometric_growth_is_monotonic(self):
        budgets = plan_round_budgets(1000, 4, schedule="geometric", ratio=2.0)
        self.assertEqual(sum(budgets), 1000)
        self.assertTrue(all(budgets[i] <= budgets[i + 1] for i in range(len(budgets) - 1)))

    def test_min_per_round_enforced(self):
        budgets = plan_round_budgets(10, 5, schedule="geometric", ratio=3.0, min_per_round=1)
        self.assertEqual(sum(budgets), 10)
        self.assertTrue(all(b >= 1 for b in budgets))

    def test_infeasible_min_per_round_raises(self):
        with self.assertRaises(ValueError):
            plan_round_budgets(3, 5, min_per_round=1)

    def test_bad_schedule_raises(self):
        with self.assertRaises(ValueError):
            round_weights(3, schedule="nope")

    def test_geometric_requires_positive_ratio(self):
        with self.assertRaises(ValueError):
            round_weights(3, schedule="geometric", ratio=0.0)


class StoppingConfigTests(unittest.TestCase):
    def test_from_campaign_config_reads_nested_values(self):
        cfg = {
            "stopping_criteria": {
                "novelty_saturation": {
                    "min_new_cluster_rate": 0.05,
                    "min_new_active_set_rate": 0.03,
                },
                "severe_false_negative_upper_confidence_tolerance": 0.01,
            }
        }
        sc = StoppingConfig.from_campaign_config(cfg)
        self.assertAlmostEqual(sc.min_new_cluster_rate, 0.05)
        self.assertAlmostEqual(sc.min_new_active_set_rate, 0.03)
        self.assertAlmostEqual(sc.severe_false_negative_upper_confidence_tolerance, 0.01)

    def test_from_empty_config_uses_defaults(self):
        sc = StoppingConfig.from_campaign_config({})
        self.assertAlmostEqual(sc.min_new_cluster_rate, 0.01)


class EvaluateStoppingTests(unittest.TestCase):
    def setUp(self):
        self.cfg = StoppingConfig(
            min_new_cluster_rate=0.01,
            min_new_active_set_rate=0.01,
            severe_false_negative_upper_confidence_tolerance=0.02,
        )

    def test_stops_when_saturated_and_reliable(self):
        metrics = RoundMetrics(
            new_cluster_rate=0.005,
            new_active_set_rate=0.005,
            severe_false_negative_upper_confidence=0.01,
        )
        decision = evaluate_stopping(metrics, self.cfg)
        self.assertTrue(decision["stop"])
        self.assertTrue(decision["novelty_saturated"])
        self.assertTrue(decision["screening_reliability_met"])

    def test_no_stop_when_novelty_still_high(self):
        metrics = RoundMetrics(
            new_cluster_rate=0.5,
            new_active_set_rate=0.5,
            severe_false_negative_upper_confidence=0.01,
        )
        decision = evaluate_stopping(metrics, self.cfg)
        self.assertFalse(decision["stop"])
        self.assertFalse(decision["novelty_saturated"])

    def test_no_stop_when_screening_unreliable(self):
        metrics = RoundMetrics(
            new_cluster_rate=0.005,
            new_active_set_rate=0.005,
            severe_false_negative_upper_confidence=0.10,
        )
        decision = evaluate_stopping(metrics, self.cfg)
        self.assertFalse(decision["stop"])
        self.assertTrue(decision["novelty_saturated"])
        self.assertFalse(decision["screening_reliability_met"])


class _FakeCampaign:
    """Minimal AdaptiveCampaign stand-in recording driver interactions."""

    def __init__(self):
        self.initialized = False
        self.rounds_run: list[dict] = []

    def initialize(self):
        self.initialized = True

    def run_round(self, round_index, candidates, budget, seed):
        self.rounds_run.append(
            {
                "round_index": round_index,
                "candidate_count": len(candidates),
                "budget": budget,
                "seed": seed,
            }
        )
        return {"round_index": round_index, "selected_count": min(len(candidates), budget)}


class GlobalSchedulerTests(unittest.TestCase):
    def test_plan_rounds_indexes_seeds_and_budgets(self):
        plan = plan_rounds(1000, 4, schedule="constant", seed_start=7, start_round=2)
        self.assertEqual([rp.round_index for rp in plan], [2, 3, 4, 5])
        self.assertEqual([rp.seed for rp in plan], [7, 8, 9, 10])
        self.assertEqual(sum(rp.budget for rp in plan), 1000)

    def test_runs_all_rounds_without_metrics_provider(self):
        campaign = _FakeCampaign()
        scheduler = GlobalScheduler(campaign, total_budget=400, rounds=4)
        result = scheduler.run(candidate_provider=lambda rp: [{"id": i} for i in range(10)])
        self.assertTrue(campaign.initialized)
        self.assertEqual(result["executed_rounds"], 4)
        self.assertFalse(result["stopped_early"])
        self.assertEqual(len(campaign.rounds_run), 4)

    def test_stops_early_when_metrics_signal_saturation(self):
        campaign = _FakeCampaign()
        scheduler = GlobalScheduler(
            campaign,
            total_budget=1000,
            rounds=5,
            stopping_config=StoppingConfig(),
        )

        def metrics_provider(round_plan, summary):
            # saturate on the third executed round (index 2)
            if round_plan.round_index >= 2:
                return RoundMetrics(0.0, 0.0, 0.0)
            return RoundMetrics(1.0, 1.0, 0.0)

        result = scheduler.run(
            candidate_provider=lambda rp: [{"id": i} for i in range(20)],
            metrics_provider=metrics_provider,
        )
        self.assertTrue(result["stopped_early"])
        self.assertEqual(result["executed_rounds"], 3)
        self.assertEqual(len(campaign.rounds_run), 3)
        self.assertIsNotNone(result["stop_reason"])


if __name__ == "__main__":
    unittest.main()
