from __future__ import annotations


def should_escalate_to_ac(candidate: dict, thresholds: dict[str, float]) -> tuple[bool, list[str]]:
    reasons: list[str] = []

    if float(candidate.get("dc_severity_score", 0.0)) >= float(thresholds.get("dc_severity", 1.0)):
        reasons.append("high_dc_severity")
    if float(candidate.get("voltage_risk_score", 0.0)) >= float(thresholds.get("voltage_risk", 1.0)):
        reasons.append("high_voltage_risk")
    if float(candidate.get("reactive_risk_score", 0.0)) >= float(thresholds.get("reactive_risk", 1.0)):
        reasons.append("high_reactive_risk")
    if float(candidate.get("novelty_score", 0.0)) >= float(thresholds.get("novelty", 1.0)):
        reasons.append("high_novelty")
    if float(candidate.get("model_uncertainty_score", 0.0)) >= float(thresholds.get("uncertainty", 1.0)):
        reasons.append("high_uncertainty")

    if bool(candidate.get("required_audit_sample", False)):
        reasons.append("required_audit_sample")
    if bool(candidate.get("required_contingency_quota", False)):
        reasons.append("required_contingency_quota")
    if bool(candidate.get("required_regime_quota", False)):
        reasons.append("required_regime_quota")

    return bool(reasons), reasons
