from __future__ import annotations

import math
from typing import Iterable


NUMERIC_FIELDS = (
    "total_active_load",
    "total_reactive_load",
    "renewable_penetration",
    "reserve_margin",
    "voltage_p10",
    "voltage_p50",
    "voltage_p90",
    "branch_loading_p90",
    "generator_p_p90",
    "generator_q_p90",
    "network_losses",
    "security_margin",
)


def descriptor_distance(a: dict, b: dict) -> float:
    acc = 0.0
    for key in NUMERIC_FIELDS:
        da = float(a.get(key, 0.0))
        db = float(b.get(key, 0.0))
        acc += (da - db) ** 2
    return math.sqrt(acc)


def min_nearest_neighbor_distance(candidate: dict, corpus: Iterable[dict]) -> float:
    distances = [descriptor_distance(candidate, other) for other in corpus]
    if not distances:
        return float("inf")
    return min(distances)
