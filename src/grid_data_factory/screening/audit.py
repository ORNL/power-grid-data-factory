from __future__ import annotations


def audit_false_negative_rates(records: list[dict], severe_threshold: float = 0.0) -> dict:
    if not records:
        return {
            "count": 0,
            "false_negative_rate": 0.0,
            "severity_weighted_false_negative_rate": 0.0,
        }

    rejected = [r for r in records if str(r.get("screening_decision", "")).lower() == "rejected"]
    severe_fn = [r for r in rejected if float(r.get("ac_security_margin", 1.0)) <= severe_threshold]

    fn_rate = len(severe_fn) / len(rejected) if rejected else 0.0
    weighted_denom = sum(abs(float(r.get("ac_security_margin", 0.0))) for r in rejected) or 1.0
    weighted_num = sum(abs(float(r.get("ac_security_margin", 0.0))) for r in severe_fn)

    return {
        "count": len(records),
        "rejected_count": len(rejected),
        "false_negative_rate": fn_rate,
        "severity_weighted_false_negative_rate": weighted_num / weighted_denom,
    }
