from __future__ import annotations


def counterfactual_distance(a: dict, b: dict, keys: tuple[str, ...]) -> float:
    return sum(abs(float(a.get(k, 0.0)) - float(b.get(k, 0.0))) for k in keys)
