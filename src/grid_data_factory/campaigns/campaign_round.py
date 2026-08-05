from __future__ import annotations

from pathlib import Path
from typing import Any
import json

from grid_data_factory.acquisition.portfolio_constraints import PortfolioConstraints
from grid_data_factory.acquisition.selector import select_ac_evaluations

from .ledgers import append_parquet_rows, write_round_summary


def run_campaign_round(
    campaign_root: Path,
    round_index: int,
    candidates: list[dict[str, Any]],
    budget: int,
    queue_fractions: dict[str, float],
    constraints: PortfolioConstraints,
    audit_seed: int,
) -> dict[str, Any]:
    selected = select_ac_evaluations(
        candidates=candidates,
        budget=budget,
        queue_fractions=queue_fractions,
        constraints=constraints,
        audit_seed=audit_seed,
    )

    # Map candidate_id -> selected row ONCE (O(len(selected))). Looking the match
    # up per candidate with next()/scan would be O(len(candidates)*len(selected))
    # ~ O(n^2); at budget=15M that is ~2e14 ops (weeks). The dict makes it O(n).
    selected_by_id = {str(x.get("candidate_id")): x for x in selected}
    decisions = []
    for cand in candidates:
        cid = str(cand.get("candidate_id"))
        match = selected_by_id.get(cid)
        picked = match is not None
        decisions.append(
            {
                "round_index": round_index,
                "candidate_id": cid,
                "selected": picked,
                "primary_selection_reason": match.get("selection_reason") if match else "not_selected",
                "selection_queue": match.get("selection_queue") if match else "none",
                "scores_at_selection_time": {
                    "novelty_score": cand.get("novelty_score"),
                    "active_constraint_score": cand.get("active_constraint_score"),
                    "security_boundary_score": cand.get("security_boundary_score"),
                    "contingency_severity_score": cand.get("contingency_severity_score"),
                    "physical_credibility_score": cand.get("physical_credibility_score"),
                    "model_uncertainty_score": cand.get("model_uncertainty_score"),
                    "estimated_compute_cost": cand.get("estimated_compute_cost"),
                },
                "random_seed": audit_seed,
            }
        )

    append_parquet_rows(campaign_root / "candidate_registry.parquet", candidates)
    append_parquet_rows(campaign_root / "acquisition_decisions.parquet", decisions)

    summary = {
        "round_index": round_index,
        "candidate_count": len(candidates),
        "budget": budget,
        "selected_count": len(selected),
        "selected_by_queue": _count_selected_by_queue(selected),
    }
    selected_path = campaign_root / "round_summaries" / f"round_{round_index:03d}_selected_candidates.jsonl"
    with selected_path.open("w", encoding="utf-8") as fh:
        for row in selected:
            fh.write(json.dumps(row, ensure_ascii=True) + "\n")

    summary_path = write_round_summary(campaign_root, round_index, summary)
    summary["summary_path"] = str(summary_path)
    summary["selected_candidates_path"] = str(selected_path)
    return summary


def _count_selected_by_queue(selected: list[dict[str, Any]]) -> dict[str, int]:
    out: dict[str, int] = {}
    for item in selected:
        key = str(item.get("selection_queue", "unknown"))
        out[key] = out.get(key, 0) + 1
    return out
