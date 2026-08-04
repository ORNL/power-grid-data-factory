from __future__ import annotations


def state_aware_contingency_score(candidate: dict, weights: dict[str, float]) -> float:
    score = 0.0
    score += float(weights.get("pre_contingency_flow", 0.0)) * float(candidate.get("pre_contingency_flow_score", 0.0))
    score += float(weights.get("reactive_reserve", 0.0)) * float(candidate.get("reactive_reserve_risk", 0.0))
    score += float(weights.get("lodf_interaction", 0.0)) * float(candidate.get("lodf_interaction_score", 0.0))
    score += float(weights.get("islanding_risk", 0.0)) * float(candidate.get("islanding_risk_score", 0.0))
    score += float(weights.get("cut_set", 0.0)) * float(candidate.get("cut_set_score", 0.0))
    return score
