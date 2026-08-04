from __future__ import annotations

from collections import defaultdict
from typing import Any, Callable

from grid_data_factory.acquisition.audit_sampler import stratified_random_order
from grid_data_factory.screening.escalation import should_escalate_to_ac

_AUDIT_STRATA_KEYS = (
    "grid_family",
    "operating_regime",
    "contingency_order",
    "dc_severity_band",
    "voltage_risk_band",
    "reactive_risk_band",
)


def severity_band(x: float) -> str:
    if x < 0.25:
        return "low"
    if x < 0.6:
        return "medium"
    return "high"


def augment_strata(cand: dict[str, Any]) -> None:
    cand.setdefault("grid_family", "unknown")
    cand.setdefault("operating_regime", "unknown")
    cand.setdefault("contingency_order", 0)
    cand.setdefault("dc_severity_band", severity_band(float(cand.get("dc_severity_score", 0.0))))
    cand.setdefault("voltage_risk_band", severity_band(float(cand.get("voltage_risk_score", 0.0))))
    cand.setdefault("reactive_risk_band", severity_band(float(cand.get("reactive_risk_score", 0.0))))


def audit_sample(rejected: list[dict[str, Any]], audit_fraction: float, seed: int) -> list[dict[str, Any]]:
    if not rejected:
        return []

    target = max(1, int(round(len(rejected) * max(0.0, min(1.0, audit_fraction)))))
    ordered = stratified_random_order(
        candidates=rejected,
        strata_keys=_AUDIT_STRATA_KEYS,
        seed=seed,
    )
    return ordered[:target]


def screen_candidates(
    candidates: list[dict[str, Any]],
    thresholds: dict[str, float],
    *,
    audit_fraction: float,
    seed: int,
    progress_every: int = 200000,
    on_progress: Callable[[int, int, int], None] | None = None,
) -> dict[str, Any]:
    accepted: list[dict[str, Any]] = []
    rejected: list[dict[str, Any]] = []

    total = len(candidates)
    for i, cand in enumerate(candidates):
        augment_strata(cand)

        should_run, reasons = should_escalate_to_ac(cand, thresholds)
        cand["screening_decision"] = "accepted" if should_run else "rejected"
        cand["screening_reasons"] = reasons
        cand["required_audit_sample"] = False

        if should_run:
            accepted.append(cand)
        else:
            rejected.append(cand)

        if on_progress and progress_every and (i + 1) % progress_every == 0:
            on_progress(i + 1, total, len(accepted))

    audited = audit_sample(rejected=rejected, audit_fraction=audit_fraction, seed=seed)
    audited_ids = {str(x.get("candidate_id")) for x in audited}

    selected_for_ac: list[dict[str, Any]] = []
    for cand in accepted + rejected:
        cid = str(cand.get("candidate_id"))
        if cid in audited_ids:
            cand["required_audit_sample"] = True
            if "audit_rejected_region" not in cand["screening_reasons"]:
                cand["screening_reasons"].append("audit_rejected_region")
            selected_for_ac.append(cand)
            continue

        if cand["screening_decision"] == "accepted":
            selected_for_ac.append(cand)

    counts_by_reason: dict[str, int] = defaultdict(int)
    for cand in selected_for_ac:
        for reason in cand.get("screening_reasons", []):
            counts_by_reason[str(reason)] += 1

    return {
        "accepted": accepted,
        "rejected": rejected,
        "audited": audited,
        "selected_for_ac": selected_for_ac,
        "selection_reason_counts": dict(counts_by_reason),
    }
