from __future__ import annotations


def targeted_generation_hint(constraint_family: str) -> list[str]:
    table = {
        "generator_pmax": [
            "increase_regional_demand",
            "increase_area_exports",
            "raise_competing_generator_costs",
        ],
        "generator_qmax": [
            "increase_local_reactive_demand",
            "reduce_reactive_support",
            "tighten_voltage_control_resources",
        ],
        "voltage_low": [
            "increase_reactive_loading",
            "reduce_shunt_support",
            "increase_transfer_distance",
        ],
        "voltage_high": [
            "reduce_local_load",
            "increase_reactive_support",
            "retain_must_run_generation",
        ],
        "branch_thermal": [
            "increase_corridor_transfer",
            "remove_parallel_paths",
            "apply_branch_derating",
        ],
    }
    return table.get(constraint_family, ["increase_operational_stress", "apply_topology_stress"])
