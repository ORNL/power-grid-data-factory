from __future__ import annotations


def reactive_risk_score(
    reactive_reserve_fraction: float,
    local_q_support_density: float,
    shunt_availability_fraction: float,
) -> float:
    risk = 0.0
    risk += max(0.0, 0.4 - float(reactive_reserve_fraction))
    risk += max(0.0, 0.3 - float(local_q_support_density))
    risk += max(0.0, 0.4 - float(shunt_availability_fraction))
    return float(risk)
