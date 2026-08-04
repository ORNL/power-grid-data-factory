from __future__ import annotations

from collections import Counter


def summarize_descriptor_diversity(descriptors: list[dict]) -> dict:
    cluster_counts = Counter(d.get("descriptor_cluster", "unknown") for d in descriptors)
    active_sets = Counter(d.get("active_constraint_signature", "") for d in descriptors)
    return {
        "sample_count": len(descriptors),
        "cluster_count": len(cluster_counts),
        "largest_cluster_size": max(cluster_counts.values()) if cluster_counts else 0,
        "unique_active_set_signatures": len(active_sets),
        "dominant_active_set_share": (
            max(active_sets.values()) / len(descriptors) if descriptors and active_sets else 0.0
        ),
    }
