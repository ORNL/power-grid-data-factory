from __future__ import annotations


def normalized_margin(value: float, lower: float | None = None, upper: float | None = None, scale: float = 1.0) -> float:
    if lower is not None and value < lower:
        return (value - lower) / max(scale, 1.0e-9)
    if upper is not None and value > upper:
        return (upper - value) / max(scale, 1.0e-9)

    if lower is None and upper is None:
        return 0.0

    if lower is None:
        return (upper - value) / max(scale, 1.0e-9)

    if upper is None:
        return (value - lower) / max(scale, 1.0e-9)

    return min((value - lower), (upper - value)) / max(scale, 1.0e-9)
