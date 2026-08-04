from __future__ import annotations

DEFAULT_STRESS_DIRECTIONS = (
    "regional_load_increase",
    "reactive_load_increase",
    "renewable_reduction",
    "generator_reduction",
    "import_increase",
    "export_increase",
    "branch_derating",
    "reactive_support_reduction",
    "reserve_reduction",
    "transformer_control_restriction",
)


def apply_stress_parameter(base_value: float, stress_gain: float, alpha: float) -> float:
    return float(base_value) * (1.0 + float(stress_gain) * float(alpha))
