from __future__ import annotations

from typing import Callable


def continuation_walk(
    evaluate: Callable[[float], dict],
    alpha_start: float,
    alpha_step: float,
    alpha_max: float,
) -> list[dict]:
    points = []
    alpha = alpha_start
    while alpha <= alpha_max:
        out = evaluate(alpha)
        out = dict(out)
        out["alpha"] = alpha
        points.append(out)
        if not bool(out.get("is_secure", True)):
            break
        alpha += alpha_step
    return points
