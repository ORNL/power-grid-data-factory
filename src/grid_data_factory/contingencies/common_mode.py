from __future__ import annotations


def has_common_mode(evidence_tags: list[str]) -> bool:
    common = {
        "common_corridor",
        "common_tower",
        "common_substation",
        "common_bus",
        "common_protection_zone",
        "weather_correlated",
    }
    return any(tag in common for tag in evidence_tags)
