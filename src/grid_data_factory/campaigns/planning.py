"""Campaign-wide budget planning: split a total AC-evaluation budget across rounds.

Given a total budget and a round count, produce a deterministic per-round budget
vector under a named schedule. The vector always sums to ``total_budget`` (after
enforcing ``min_per_round``), so downstream round selection consumes the whole
budget without drift.
"""
from __future__ import annotations

SCHEDULES = ("constant", "linear", "geometric")


def round_weights(rounds: int, schedule: str = "constant", ratio: float = 1.0) -> list[float]:
    """Relative per-round weights for a schedule.

    - ``constant``: equal weight every round.
    - ``linear``: weight ``1 + i * ratio`` (ratio is per-round growth; may be negative
      as long as all weights stay positive).
    - ``geometric``: weight ``ratio ** i`` (ratio > 0).
    """
    if rounds <= 0:
        raise ValueError("rounds must be > 0")
    if schedule not in SCHEDULES:
        raise ValueError(f"Unknown schedule {schedule!r}; expected one of {SCHEDULES}")

    if schedule == "constant":
        weights = [1.0] * rounds
    elif schedule == "linear":
        weights = [1.0 + i * float(ratio) for i in range(rounds)]
    else:  # geometric
        if ratio <= 0.0:
            raise ValueError("geometric schedule requires ratio > 0")
        weights = [float(ratio) ** i for i in range(rounds)]

    if any(w <= 0.0 for w in weights):
        raise ValueError(f"schedule {schedule!r} with ratio {ratio} produced a non-positive weight")
    return weights


def plan_round_budgets(
    total_budget: int,
    rounds: int,
    schedule: str = "constant",
    ratio: float = 1.0,
    min_per_round: int = 1,
) -> list[int]:
    """Split ``total_budget`` into ``rounds`` positive integers summing to the total.

    Weights come from :func:`round_weights`. Fractional allocations are floored and
    the leftover units are handed to the largest fractional remainders (deterministic,
    largest-remainder method). ``min_per_round`` guarantees every round runs.
    """
    if rounds <= 0:
        raise ValueError("rounds must be > 0")
    if min_per_round < 0:
        raise ValueError("min_per_round must be >= 0")
    if total_budget < rounds * min_per_round:
        raise ValueError(
            f"total_budget {total_budget} cannot satisfy min_per_round {min_per_round} across {rounds} rounds"
        )

    reserved = min_per_round * rounds
    distributable = total_budget - reserved

    weights = round_weights(rounds, schedule=schedule, ratio=ratio)
    weight_sum = sum(weights)

    exact = [distributable * (w / weight_sum) for w in weights]
    floors = [int(x) for x in exact]
    allocated = sum(floors)
    leftover = distributable - allocated

    remainders = sorted(range(rounds), key=lambda i: exact[i] - floors[i], reverse=True)
    for i in remainders[:leftover]:
        floors[i] += 1

    return [floors[i] + min_per_round for i in range(rounds)]
