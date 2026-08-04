from __future__ import annotations


def novelty_saturation(
    newly_discovered_cluster_rate: float,
    newly_discovered_active_set_rate: float,
    min_cluster_rate: float,
    min_active_set_rate: float,
) -> bool:
    return (
        float(newly_discovered_cluster_rate) <= float(min_cluster_rate)
        and float(newly_discovered_active_set_rate) <= float(min_active_set_rate)
    )


def screening_reliability_met(
    severe_false_negative_upper_confidence: float,
    tolerance: float,
) -> bool:
    return float(severe_false_negative_upper_confidence) <= float(tolerance)
