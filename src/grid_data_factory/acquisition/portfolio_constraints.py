from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass
class PortfolioConstraints:
    max_per_grid: int = 0
    max_per_regime: int = 0
    max_per_contingency_class: int = 0


def _count_matches(selected: list[dict[str, Any]], key: str, value: Any) -> int:
    return sum(1 for item in selected if item.get(key) == value)


def violates_portfolio_constraints(
    candidate: dict[str, Any],
    selected: list[dict[str, Any]],
    constraints: PortfolioConstraints,
) -> bool:
    if constraints.max_per_grid > 0:
        if _count_matches(selected, "grid_id", candidate.get("grid_id")) >= constraints.max_per_grid:
            return True

    if constraints.max_per_regime > 0:
        if _count_matches(selected, "operating_regime", candidate.get("operating_regime")) >= constraints.max_per_regime:
            return True

    if constraints.max_per_contingency_class > 0:
        if (
            _count_matches(selected, "contingency_class", candidate.get("contingency_class"))
            >= constraints.max_per_contingency_class
        ):
            return True

    return False
