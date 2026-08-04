from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any


@dataclass
class SolvedStateDescriptor:
    candidate_id: str
    total_active_load: float
    total_reactive_load: float
    renewable_penetration: float
    reserve_margin: float
    voltage_p10: float
    voltage_p50: float
    voltage_p90: float
    branch_loading_p90: float
    generator_p_p90: float
    generator_q_p90: float
    network_losses: float
    active_constraint_signature: str
    near_active_constraint_signature: str
    active_constraint_count: int
    near_active_constraint_count: int
    contingency_order: int
    topology_class: str
    security_margin: float

    def to_record(self) -> dict[str, Any]:
        return asdict(self)
