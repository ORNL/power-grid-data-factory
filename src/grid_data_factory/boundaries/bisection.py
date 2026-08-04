from __future__ import annotations

from typing import Callable


def bisect_transition(
    predicate: Callable[[float], bool],
    lo: float,
    hi: float,
    iterations: int = 20,
) -> float:
    for _ in range(iterations):
        mid = 0.5 * (lo + hi)
        if predicate(mid):
            lo = mid
        else:
            hi = mid
    return 0.5 * (lo + hi)
