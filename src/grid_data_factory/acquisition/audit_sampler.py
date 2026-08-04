from __future__ import annotations

from random import Random
from typing import Any


def stratified_random_order(
    candidates: list[dict[str, Any]],
    strata_keys: tuple[str, ...],
    seed: int,
) -> list[dict[str, Any]]:
    rng = Random(seed)
    by_stratum: dict[tuple[Any, ...], list[dict[str, Any]]] = {}
    for cand in candidates:
        key = tuple(cand.get(k) for k in strata_keys)
        by_stratum.setdefault(key, []).append(cand)

    interleaved: list[dict[str, Any]] = []
    strata = list(by_stratum.values())
    for bucket in strata:
        rng.shuffle(bucket)

    while strata:
        next_strata: list[list[dict[str, Any]]] = []
        for bucket in strata:
            if bucket:
                interleaved.append(bucket.pop())
            if bucket:
                next_strata.append(bucket)
        strata = next_strata

    return interleaved
