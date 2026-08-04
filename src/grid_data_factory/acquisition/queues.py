from __future__ import annotations

from typing import Any

from .audit_sampler import stratified_random_order
from .uncertainty import severity_upper_bound

QUEUE_NAMES = (
    "coverage",
    "active_set",
    "boundary",
    "credible_contingency",
    "severity_uncertainty",
    "audit",
)


def _sort_desc(candidates: list[dict[str, Any]], score_key: str) -> list[dict[str, Any]]:
    return sorted(candidates, key=lambda c: float(c.get(score_key, 0.0)), reverse=True)


def rank_by_novelty(candidates: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return _sort_desc(candidates, "novelty_score")


def rank_by_constraint_deficit(candidates: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return _sort_desc(candidates, "active_constraint_score")


def rank_by_boundary_score(candidates: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return _sort_desc(candidates, "security_boundary_score")


def rank_by_credibility_and_diversity(candidates: list[dict[str, Any]]) -> list[dict[str, Any]]:
    scored = []
    for cand in candidates:
        cred = float(cand.get("physical_credibility_score", 0.0))
        sev = float(cand.get("contingency_severity_score", 0.0))
        nov = float(cand.get("novelty_score", 0.0))
        score = 0.5 * cred + 0.3 * sev + 0.2 * nov
        out = dict(cand)
        out["_queue_score"] = score
        scored.append(out)
    return _sort_desc(scored, "_queue_score")


def rank_by_severity_upper_bound(candidates: list[dict[str, Any]], uncertainty_multiplier: float = 1.96) -> list[dict[str, Any]]:
    scored = []
    for cand in candidates:
        out = dict(cand)
        out["_queue_score"] = severity_upper_bound(
            predicted_severity=float(cand.get("contingency_severity_score", 0.0)),
            uncertainty=float(cand.get("model_uncertainty_score", 0.0)),
            multiplier=uncertainty_multiplier,
        )
        scored.append(out)
    return _sort_desc(scored, "_queue_score")


def build_queues(
    candidates: list[dict[str, Any]],
    audit_strata: tuple[str, ...],
    audit_seed: int,
    uncertainty_multiplier: float = 1.96,
) -> dict[str, list[dict[str, Any]]]:
    return {
        "coverage": rank_by_novelty(candidates),
        "active_set": rank_by_constraint_deficit(candidates),
        "boundary": rank_by_boundary_score(candidates),
        "credible_contingency": rank_by_credibility_and_diversity(candidates),
        "severity_uncertainty": rank_by_severity_upper_bound(candidates, uncertainty_multiplier),
        "audit": stratified_random_order(candidates, strata_keys=audit_strata, seed=audit_seed),
    }
