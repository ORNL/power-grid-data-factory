from __future__ import annotations


def compute_security_margin(normalized_margins: dict[str, float]) -> float:
    if not normalized_margins:
        return 0.0
    return min(float(v) for v in normalized_margins.values())


def classify_security_margin_band(security_margin: float, bands: dict[str, dict[str, float]]) -> str:
    for band_name, rules in bands.items():
        minimum = rules.get("minimum_margin", float("-inf"))
        maximum = rules.get("maximum_margin", float("inf"))
        if minimum <= security_margin <= maximum:
            return band_name
    return "unclassified"
