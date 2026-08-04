from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any


@dataclass
class DCScreeningFeatures:
    candidate_id: str
    dc_objective: float
    dc_branch_margin_min: float
    dc_congestion_count: int
    dc_islanded: bool
    dc_severity_score: float

    def to_record(self) -> dict[str, Any]:
        return asdict(self)
