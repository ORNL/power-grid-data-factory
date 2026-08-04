from __future__ import annotations


def severity_upper_bound(predicted_severity: float, uncertainty: float, multiplier: float = 1.96) -> float:
    """Upper-confidence severity score used for conservative acquisition."""
    return float(predicted_severity) + float(multiplier) * float(uncertainty)
