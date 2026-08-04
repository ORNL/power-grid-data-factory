from __future__ import annotations


def voltage_risk_score(
    min_voltage: float,
    q_headroom_fraction: float,
    tap_saturation_fraction: float,
    jacobian_condition_indicator: float,
) -> float:
    risk = 0.0
    risk += max(0.0, 1.0 - float(min_voltage))
    risk += max(0.0, 0.5 - float(q_headroom_fraction))
    risk += 0.5 * float(tap_saturation_fraction)
    risk += 0.5 * float(jacobian_condition_indicator)
    return float(risk)
