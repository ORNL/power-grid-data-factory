from __future__ import annotations

from typing import Any

from .portfolio_constraints import PortfolioConstraints, violates_portfolio_constraints
from .queues import QUEUE_NAMES, build_queues


def _round_budget(total: int, fraction: float) -> int:
    return max(0, int(round(total * float(fraction))))


def _seen_ids(selected: list[dict[str, Any]]) -> set[str]:
    return {str(item.get("candidate_id")) for item in selected}


def assign_selection_reason(candidate: dict[str, Any], queue_name: str) -> dict[str, Any]:
    out = dict(candidate)
    out["selection_queue"] = queue_name
    out["selection_reason"] = f"selected_from_{queue_name}_queue"
    out["selection_status"] = "selected"
    return out


def deduplicate_and_backfill(selected: list[dict[str, Any]], candidates: list[dict[str, Any]], target_count: int) -> list[dict[str, Any]]:
    ids = _seen_ids(selected)
    if len(selected) >= target_count:
        return selected[:target_count]

    for cand in sorted(candidates, key=lambda c: float(c.get("novelty_score", 0.0)), reverse=True):
        cid = str(cand.get("candidate_id"))
        if cid in ids:
            continue
        item = assign_selection_reason(cand, "backfill")
        selected.append(item)
        ids.add(cid)
        if len(selected) >= target_count:
            break

    return selected


def select_ac_evaluations(
    candidates: list[dict[str, Any]],
    budget: int,
    queue_fractions: dict[str, float],
    constraints: PortfolioConstraints | None = None,
    audit_seed: int = 0,
    audit_strata: tuple[str, ...] = (
        "grid_family",
        "operating_regime",
        "contingency_order",
        "dc_severity_band",
        "voltage_risk_band",
        "reactive_risk_band",
    ),
    uncertainty_multiplier: float = 1.96,
) -> list[dict[str, Any]]:
    constraints = constraints or PortfolioConstraints()

    missing = [name for name in QUEUE_NAMES if name not in queue_fractions]
    if missing:
        raise ValueError(f"Missing queue fractions for: {missing}")

    # Fast path: when the budget covers every candidate, the per-queue ranking is
    # irrelevant — every candidate is selected regardless of order. Skip
    # build_queues, which does 6 full sorts plus a dict() copy of *every*
    # candidate in two of the queues (~2n copies). At n=15M that path costs
    # ~20 min and ~190 GB RAM; this one is a single O(n) pass. The result is
    # equivalent to the full path (all constraint-passing candidates, capped at
    # budget >= n) apart from the queue label.
    if budget >= len(candidates):
        selected = []
        selected_ids = set()
        for candidate in candidates:
            cid = str(candidate.get("candidate_id"))
            if cid in selected_ids:
                continue
            if violates_portfolio_constraints(candidate, selected, constraints):
                continue
            selected.append(assign_selection_reason(candidate, "full_budget"))
            selected_ids.add(cid)
        return selected

    queues = build_queues(
        candidates=candidates,
        audit_strata=audit_strata,
        audit_seed=audit_seed,
        uncertainty_multiplier=uncertainty_multiplier,
    )

    selected: list[dict[str, Any]] = []
    selected_ids: set[str] = set()

    for queue_name in QUEUE_NAMES:
        queue_budget = _round_budget(budget, queue_fractions[queue_name])
        queue_selected = 0

        for candidate in queues[queue_name]:
            if queue_selected >= queue_budget:
                break

            cid = str(candidate.get("candidate_id"))
            if cid in selected_ids:
                continue

            if violates_portfolio_constraints(candidate, selected, constraints):
                continue

            selected_item = assign_selection_reason(candidate, queue_name)
            selected.append(selected_item)
            selected_ids.add(cid)
            queue_selected += 1

    return deduplicate_and_backfill(selected=selected, candidates=candidates, target_count=budget)
