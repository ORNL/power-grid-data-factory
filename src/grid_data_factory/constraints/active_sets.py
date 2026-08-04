from __future__ import annotations


def classify_activation(margin: float, active_tolerance: float, near_active_tolerance: float) -> int:
    if margin <= active_tolerance:
        return 2
    if margin <= near_active_tolerance:
        return 1
    return 0


def build_active_constraint_signature(
    margins: dict[str, float],
    active_tolerance: float = 1.0e-4,
    near_active_tolerance: float = 5.0e-3,
) -> dict[str, int]:
    return {
        key: classify_activation(float(val), active_tolerance=active_tolerance, near_active_tolerance=near_active_tolerance)
        for key, val in margins.items()
    }
