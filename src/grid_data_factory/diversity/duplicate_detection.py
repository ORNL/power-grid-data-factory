from __future__ import annotations

from .nearest_neighbors import min_nearest_neighbor_distance


def classify_duplicate_status(
    descriptor: dict,
    accepted_descriptors: list[dict],
    near_duplicate_threshold: float = 0.02,
) -> tuple[str, float]:
    dmin = min_nearest_neighbor_distance(descriptor, accepted_descriptors)
    if dmin == float("inf"):
        return "new", dmin
    if dmin <= near_duplicate_threshold:
        return "near_duplicate", dmin
    return "novel", dmin
