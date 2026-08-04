"""Global multi-round campaign scheduler.

Plans a campaign as a sequence of rounds (each with a seed and a planned budget),
drives selection round-by-round through :class:`AdaptiveCampaign`, and evaluates
the configured stopping criteria after each round so a campaign halts once novelty
has saturated and screening reliability is met.

The scheduler is execution-agnostic: candidate pools and post-execution round
metrics are supplied through callbacks, so the same driver works for in-process
selection or for Slurm map/reduce rounds that report metrics back from the reduce
stage.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Optional

from .planning import plan_round_budgets
from .stopping import novelty_saturation, screening_reliability_met


@dataclass(frozen=True)
class StoppingConfig:
    min_new_cluster_rate: float = 0.01
    min_new_active_set_rate: float = 0.01
    severe_false_negative_upper_confidence_tolerance: float = 0.02

    @staticmethod
    def from_campaign_config(config: dict[str, Any]) -> "StoppingConfig":
        sc = (config or {}).get("stopping_criteria", {}) or {}
        ns = sc.get("novelty_saturation", {}) or {}
        return StoppingConfig(
            min_new_cluster_rate=float(ns.get("min_new_cluster_rate", 0.01)),
            min_new_active_set_rate=float(ns.get("min_new_active_set_rate", 0.01)),
            severe_false_negative_upper_confidence_tolerance=float(
                sc.get("severe_false_negative_upper_confidence_tolerance", 0.02)
            ),
        )


@dataclass(frozen=True)
class RoundMetrics:
    """Post-execution discovery metrics for a completed round."""

    new_cluster_rate: float
    new_active_set_rate: float
    severe_false_negative_upper_confidence: float


@dataclass(frozen=True)
class RoundPlan:
    round_index: int
    seed: int
    budget: int


def evaluate_stopping(metrics: RoundMetrics, config: StoppingConfig) -> dict[str, Any]:
    """Return the stopping decision for a round given its metrics."""
    saturated = novelty_saturation(
        newly_discovered_cluster_rate=metrics.new_cluster_rate,
        newly_discovered_active_set_rate=metrics.new_active_set_rate,
        min_cluster_rate=config.min_new_cluster_rate,
        min_active_set_rate=config.min_new_active_set_rate,
    )
    reliable = screening_reliability_met(
        severe_false_negative_upper_confidence=metrics.severe_false_negative_upper_confidence,
        tolerance=config.severe_false_negative_upper_confidence_tolerance,
    )
    return {
        "stop": bool(saturated and reliable),
        "novelty_saturated": bool(saturated),
        "screening_reliability_met": bool(reliable),
    }


def plan_rounds(
    total_budget: int,
    rounds: int,
    schedule: str = "constant",
    ratio: float = 1.0,
    seed_start: int = 0,
    start_round: int = 0,
    min_per_round: int = 1,
) -> list[RoundPlan]:
    """Produce the per-round plan (index, seed, budget) for a campaign."""
    budgets = plan_round_budgets(
        total_budget=total_budget,
        rounds=rounds,
        schedule=schedule,
        ratio=ratio,
        min_per_round=min_per_round,
    )
    return [
        RoundPlan(round_index=start_round + i, seed=seed_start + i, budget=budgets[i])
        for i in range(rounds)
    ]


# candidate_provider(round_plan) -> list of candidate dicts for that round.
CandidateProvider = Callable[[RoundPlan], list[dict[str, Any]]]
# metrics_provider(round_plan, round_summary) -> RoundMetrics, or None if unavailable.
MetricsProvider = Callable[[RoundPlan, dict[str, Any]], Optional[RoundMetrics]]


class GlobalScheduler:
    """Drives an :class:`AdaptiveCampaign` across a planned sequence of rounds."""

    def __init__(
        self,
        campaign: Any,
        total_budget: int,
        rounds: int,
        schedule: str = "constant",
        ratio: float = 1.0,
        seed_start: int = 0,
        start_round: int = 0,
        min_per_round: int = 1,
        stopping_config: StoppingConfig | None = None,
    ):
        self.campaign = campaign
        self.plan = plan_rounds(
            total_budget=total_budget,
            rounds=rounds,
            schedule=schedule,
            ratio=ratio,
            seed_start=seed_start,
            start_round=start_round,
            min_per_round=min_per_round,
        )
        self.stopping_config = stopping_config or StoppingConfig()

    def run(
        self,
        candidate_provider: CandidateProvider,
        metrics_provider: MetricsProvider | None = None,
    ) -> dict[str, Any]:
        """Run planned rounds in order, stopping early when criteria are met.

        Returns a campaign-level summary listing each executed round, its selection
        summary, and any stopping decision. When ``metrics_provider`` is ``None`` the
        campaign runs every planned round (no early stop).
        """
        self.campaign.initialize()

        executed: list[dict[str, Any]] = []
        stopped_early = False
        stop_reason: dict[str, Any] | None = None

        for round_plan in self.plan:
            candidates = candidate_provider(round_plan)
            summary = self.campaign.run_round(
                round_index=round_plan.round_index,
                candidates=candidates,
                budget=round_plan.budget,
                seed=round_plan.seed,
            )

            record: dict[str, Any] = {
                "round_index": round_plan.round_index,
                "seed": round_plan.seed,
                "planned_budget": round_plan.budget,
                "summary": summary,
            }

            if metrics_provider is not None:
                metrics = metrics_provider(round_plan, summary)
                if metrics is not None:
                    decision = evaluate_stopping(metrics, self.stopping_config)
                    record["stopping_decision"] = decision
                    if decision["stop"]:
                        stopped_early = True
                        stop_reason = decision
                        executed.append(record)
                        break

            executed.append(record)

        return {
            "planned_rounds": len(self.plan),
            "executed_rounds": len(executed),
            "stopped_early": stopped_early,
            "stop_reason": stop_reason,
            "total_planned_budget": sum(rp.budget for rp in self.plan),
            "rounds": executed,
        }
