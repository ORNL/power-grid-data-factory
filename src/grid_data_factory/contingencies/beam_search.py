from __future__ import annotations

from typing import Any, Callable


def constrained_beam_search(
    seed_events: list[dict[str, Any]],
    expand: Callable[[dict[str, Any]], list[dict[str, Any]]],
    score: Callable[[dict[str, Any]], float],
    credible: Callable[[dict[str, Any]], bool],
    beam_width: int,
    target_order: int,
) -> list[dict[str, Any]]:
    beam = [e for e in seed_events if credible(e)]
    for _ in range(1, target_order):
        expanded: list[dict[str, Any]] = []
        for event in beam:
            for child in expand(event):
                if credible(child):
                    expanded.append(child)
        expanded.sort(key=score, reverse=True)
        beam = expanded[:beam_width]
        if not beam:
            break
    return beam
